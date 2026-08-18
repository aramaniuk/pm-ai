## **title: Local-First AI PM Assistant (pm-ai)**

version: 0.7.0  
created: 2026-08-16  
updated: 2026-08-17  
status: draft

# **PRD: Local-First AI PM Assistant (pm-ai)**

## **0\. Document Purpose**

This PRD defines the functional capabilities, behavioral boundaries, and technical architecture for pm-ai, an executive personal PM coach, mobile voice concierge, and sovereign career companion running locally under $20/month. It completely replaces legacy cloud RAG architectures (AWS \+ Onyx @ $800+/month) with a git-backed, markdown-driven operating system designed to eradicate managerial cognitive tax, protect executive bandwidth, and continuously align daily micro-decisions across three goal horizons: Project(s), Team(s), and Personal/Career Growth.

## **1\. Vision**

pm-ai is a local-first, privacy-preserving Executive Operating System and Socratic PM Companion. Rather than acting as an open-ended conversational chatbot or a noisy notification relay, pm-ai silently harvests telemetry across GitLab, Teams, Outlook Calendar, Telegram, HR tools, Slack, Jira, Notion, and extensible third-party platforms. It enables high-context voice synthesis, delivers pre-rendered focus briefings before scheduled meetings, handles deep asynchronous cross-telemetry queries, synthesizes telemetry-enriched daily standup and meeting preparation dashboards, parses structured spoken protocols during live meetings, executes automated research tasks, and facilitates structured, telemetry-backed 1:1 Socratic retrospectives. Dual access is provided via a mobile Telegram voice/text bridge and a terminal-native interactive CLI console for local desktop execution. All persistent career records, coaching logs, and personal rules remain strictly sovereign in local Markdown files, ensuring complete portability and zero vendor lock-in.

## **2\. Target User & Scope Isolation**

### **2.1 Dual-Scope Architecture**

> * **Sovereign Personal PM Scope (\~/.manager-ai/):** Independent personal coaching hub containing leadership philosophy (manager\_principles.md), 3-tier goals (strategic\_goals.md), Socratic 1:1 coaching logs (coaching\_1on1\_history.md), literature and web page subscriptions (article\_sources.md), and anti-burnout metrics. This scope survives independently across project, role, or company transitions.  
> * **Isolated Project Scopes (\<project-root\>/.project-ai/):** Repository-specific directory committed to version control, containing project-specific rules, task automation scripts, team cultural rules/conventions, local daily project dashboards, and the team meeting commitments ledger.

\================================================================================  
A. SOVEREIGN PERSONAL PM SCOPE (\~/.manager-ai/ or \~/.pm-profile/)  
\================================================================================  
.manager-ai/                            \# INDEPENDENT PERSONAL PM COACHING HUB  
│  
├── rules/  
│   ├── manager\_principles.md           \# Personal leadership philosophy & career guidelines  
│   ├── persona.md                      \# Personal coach persona, tone & constructiveness level  
│   ├── communication\_preferences.md    \# Executive briefing preferences & voice triggers  
│   └── article\_sources.md              \# PM-configurable literature & web HTTP sources (FR-17)  
│  
├── memory/  
│   ├── daily\_dashboard.md              \# Manager Strategic Focus Morning Briefing (FR-09)  
│   ├── strategic\_goals.md              \# 3-Tier Goals (Project, Team, Personal Career Goals)  
│   ├── coaching\_1on1\_history.md        \# Socratic 1:1 logs, meta-feedback & growth notes (FR-12)  
│   └── event\_log.md                    \# Multi-project master audit trail & decision log (FR-10, FR-27)  
│  
└── skills/                             \# PERSONAL CONCIERGE & CAREER SKILLS  
    ├── telemetry/                      \# Global cross-project telemetry harvesters & connectors  
    ├── team\_member\_career\_mcp.py       \# MCP connector to external HR platforms (FR-31)  
    ├── synthesize\_manager\_dashboard.py \# Manager Strategic Focus generator  
    └── anti\_burnout\_shield.py          \# Workload telemetry & PTO guardrail analyzer (FR-16)

.manager-ai-private/                    \# PRIVATE SCRATCHPAD & SECRETS (Gitignored)  
├── event\_telemetry.db                  \# Ephemeral cross-project SQLite telemetry DB & index  
├── chat\_history/                       \# Global session logs & audio transcripts  
├── telegram\_cache/                     \# Mobile voice notes & conversation state  
└── config.json                         \# Encrypted API credentials (GitLab, Teams, Telegram, HR MCP, Jira, Slack, Notion)

\================================================================================  
B. ISOLATED PROJECT SCOPES (\<project-repository-root\>/.project-ai/)  
\================================================================================  
\<project-repository-alpha\>/  
│  
├── .project-ai/                        \# PROJECT ALPHA SPECIFIC CONTEXT (Committed to Git)  
│   ├── rules/  
│   │   ├── persona.md                  \# Project Alpha assistant persona definition (FR-20)  
│   │   ├── conventions.md              \# Project Alpha team cultural rules  
│   │   └── engineering\_specs.md        \# Architecture & code guidelines  
│   ├── memory/  
│   │   ├── daily\_dashboard.md          \# Project Alpha Daily Team Dashboard  
│   │   ├── commitments\_log.md          \# Spoken commitments & promise tracking ledger (FR-34)  
│   │   └── event\_log.md                \# Project Alpha specific audit trail & decision log (FR-27)  
│   └── skills/                         \# PROJECT ALPHA SPECIFIC SKILLS  
│       ├── parse\_standup.py  
│       └── sync\_gitlab\_wi.py  
│  
└── .gitignore                          \# Contains .project-ai-private/

### **2.2 Jobs To Be Done**

> * **JTBD-1 (Context-Enriched Delegation & Communication):** Draft detailed, high-context replies across tools from brief voice or text instructions without manually searching tickets, specs, or spreadsheets.  
> * **JTBD-2 (Socratic Growth):** Surface unexamined leadership blind spots, time misallocations, and strategic drift through telemetry-backed Socratic dialogue.  
> * **JTBD-3 (Contextual Learning):** Receive situational literature citations and leadership frameworks from curated RSS feeds and HTTP web pages at the exact moment operational challenges arise.  
> * **JTBD-4 (Frictionless Meeting Accountability):** Materialize spoken meeting commitments directly into GitLab Work Items and track meeting ROI without noisy reminder alarms.  
> * **JTBD-5 (Career Cultivation):** Review synthesized pre-meeting engineering dossiers combining sprint output, monitored custom metrics, and long-term HR growth goals before 1:1s.  
> * **JTBD-6 (Deep Asynchronous Information & Telemetry Query):** Query pm-ai on-demand via voice, Telegram, or CLI for multi-source activity breakdowns, historical meeting decisions, DevOps operational procedures, and documentation consistency checks without logging into multiple tools.  
> * **JTBD-7 (Telemetry-Enriched Meeting Preparation):** Prepare for scheduled meetings (daily standups, architecture syncs, planning) with auto-researched participant activity and agenda analysis starting 15 minutes prior (or at least 1 hour prior if automated owner inquiries via FR-26 are required) tailored to meeting type, agenda, and attendees.  
> * **JTBD-8 (Zero-Friction In-Meeting Task Automation & Research Execution):** Issue direct verbal commands and complex research instructions to pm-ai by name during meetings to instantly mutate Work Items, record explicit priorities, and dispatch async background research without breaking conversation flow.  
> * **JTBD-9 (Dual-Layer Meeting Authorization & Missed Meeting Analysis):** Differentiate between explicit in-meeting commands (auto-executed) and implicit discussion extractions (staged via Telegram/CLI approval cards with parsed/suggested Work Item metadata), while enabling on-demand transcript ingestion for missed or optional meetings.  
> * **JTBD-10 (Terminal-Native Interactive CLI Console Access):** Launch local interactive console sessions (pm-ai) to execute commands, run open-text natural language prompts in an interactive REPL shell, and trigger background skills with full feature parity to the Telegram text interface.  
> * **JTBD-11 (Mindful Multi-Horizon Planning & Burnout Prevention):** Mindfully calibrate weekly and daily work schedules by mapping operational tasks and tactical deliverables against 3-tier long/middle/short-term strategic goals, while actively capping calendar density to mitigate burnout risks.  
> * **JTBD-12 (Extensible Telemetry & Multi-Tool Connector Lifecycle):** Dynamically configure, enable, disable, and expand external telemetry connectors (GitLab, Teams, Outlook, HR systems, Slack, Jira, Notion) via CLI or Telegram interfaces with encrypted credential management and hot-swappable schema normalization.

### **2.3 Key User Journeys**

> * **UJ-1. Andrei runs a weekly Socratic 1:1 session via Telegram or CLI.**  
  * **Persona \+ context:** Andrei (Engineering PM) conducting his weekly leadership retrospective on a Friday afternoon.  
  * **Entry state:** Authenticated on Telegram or in terminal session (pm-ai console or interactive pm-ai). Sends a text message: *"Let's start our weekly 1:1 session."*  
  * **Path:**  
    1. pm-ai opens the session with a concise telemetry breakdown showing actual time allocation vs. Q3 strategic goals (e.g., 80% time spent debugging ticket specs vs. 20% on delegation).  
    2. pm-ai evaluates the Anti-Burnout Telemetry Shield and flags elevated workload patterns (e.g., 3 consecutive 10-hour days).  
    3. pm-ai asks a targeted Socratic question: *"What specific blocker in Project Alpha prevented you from handing off the auth refactor to Alex this week?"*  
    4. Andrei reflects via text or voice note on why he hesitated to delegate.  
    5. pm-ai references an article or HTTP page on engineering delegation frameworks from article\_sources.md with a direct citation and suggests an actionable delegation experiment for next sprint.  
  * **Climax:** Andrei commits to the experiment; pm-ai logs the decision in event\_log.md and coaching\_1on1\_history.md, then generates a 2-question Meta-Coaching Scorecard prompt (1-10 rating scale).  
  * **Resolution:** Andrei rates the session in 5 seconds; pm-ai updates its internal persona tuning without interrupting live work.  
> * **UJ-2. Andrei drafts high-context technical responses via a 20-second voice note.**  
  * **Persona \+ context:** Andrei away from his desk needing to give detailed technical direction to multiple team members.  
  * **Entry state:** Opens Telegram voice channel.  
  * **Path:**  
    1. Andrei speaks a single 20-second voice note: *"Draft a reply to Laura explaining the webhook contract changes in v2.1 based on yesterday's architecture meeting, and tell Alex to proceed with Schema B for Auth caching."*  
    2. pm-ai processes the audio, cross-references recent meeting transcripts, architecture specs, and active GitLab work items.  
    3. pm-ai generates individual draft cards in Telegram one by one, displaying target recipient, full enriched draft body, and cited source artifacts.  
    4. Andrei reviews Draft 1 (Laura), taps \[Send\]; reviews Draft 2 (Alex), taps \[Edit\], tweaks a sentence, and taps \[Send\].  
  * **Climax:** High-context technical responses are delivered directly to Laura's email and Alex's Teams thread without Andrei manually opening spreadsheets or specs.  
  * **Resolution:** Andrei clears a complex communication queue in 60 seconds of mobile interaction.  
> * **UJ-3. Andrei wraps a meeting with zero administrative fallout.**  
  * **Persona \+ context:** Andrei finishing a 45-minute Project Alpha architecture sync.  
  * **Entry state:** Meeting ends in Outlook Calendar.  
  * **Path:**  
    1. pm-ai automatically downloads and processes the meeting transcript within 600 seconds. It calculates Meeting Man-Hour Cost (![][image1]) and includes this metric in the summary card header.  
    2. pm-ai parses explicit direct commands issued during the meeting and auto-executes them, including an explicit confirmation section in the post-meeting report.  
    3. pm-ai extracts Alex's spoken commitment (*"I'll finish Redis benchmarks by Thursday"*) and stages an interactive card proposing to append a timestamped comment to GitLab Work Item \#102, while writing the commitment entry to .project-ai/memory/commitments\_log.md.  
  * **Climax:** GitLab Work Items and local commitments log reflect real discussion state without Andrei opening a single ticket editor or setting manual alarms.  
  * **Resolution:** Andrei transitions to his next task with zero manual note-taking overhead.  
> * **UJ-4. Andrei prepares for and completes a team member 1:1 sync.**  
  * **Persona \+ context:** Andrei conducting a scheduled 1:1 career and performance sync with Alex.  
  * **Entry state:** Outlook Calendar event trigger (15 minutes prior to session).  
  * **Path:**  
    1. pm-ai queries local GitLab telemetry (MR velocity, PR review participation), fetches custom monitored metric dynamics (e.g., issues found per MR review over Q3), and connects via MCP skill to HR tools.  
    2. pm-ai synthesizes a compact **Career Dossier** and pushes it as a pre-meeting Telegram message or local CLI output.  
    3. Andrei conducts the 1:1 grounded with objective data and aligned growth goals.  
  * **Climax:** Post-meeting, pm-ai automatically fetches and processes the recorded 1:1 meeting transcript, extracting agreed career goals and performance objectives.  
  * **Resolution:** pm-ai presents the extracted goals to Andrei for approval; upon his explicit approval, pm-ai syncs the agreed goals to the HR platform via MCP.  
> * **UJ-5. Andrei executes an asynchronous deep knowledge and telemetry inquiry via Telegram or CLI.**  
  * **Persona \+ context:** Andrei needs instant clarity on team member activity, past architectural decisions, DevOps environment instructions, or specification drift while at his workstation or mobile.  
  * **Entry state:** Opens Telegram or terminal prompt (pm-ai query or active pm-ai REPL session) and inputs prompt: *"What activity did Alex do yesterday? Show me focus across commits, WI updates, CI/CD, MRs, and calendar events"* OR *"Find information about the auth protocol discussed recently and verify if it's in sync with documentation"*.  
  * **Path:**  
    1. pm-ai acknowledges the query with an immediate status token (e.g., \[⏳ Processing deep query...\]).  
    2. The local daemon executes cross-source telemetry harvesters across event\_telemetry.db, local meeting transcript logs, and project rules in .project-ai/rules/engineering\_specs.md.  
    3. For documentation synchronization queries, pm-ai diffs verbal transcript decisions against committed Markdown specifications.  
    4. pm-ai presents a structured response card in Telegram or formatted Markdown terminal output categorizing key findings, cited artifacts, timestamped commits/events, and identified documentation drift flags.  
  * **Climax:** Andrei receives a comprehensive, multi-source synthesized response directly on mobile or CLI without logging into five separate developer tools or web portals.  
  * **Resolution:** Andrei either responds immediately to a blocker or issues a single CLI/Telegram command to update out-of-sync repository documentation.  
> * **UJ-6. Andrei prepares for and runs a telemetry-enriched daily standup or team meeting in seconds.**  
  * **Persona \+ context:** Andrei preparing for a scheduled team meeting (e.g., morning 15-minute daily standup or architecture review).  
  * **Entry state:** Scheduled calendar event trigger (15 minutes prior, or 1 hour prior if automated inquiries are required).  
  * **Path:**  
    1. pm-ai evaluates meeting agenda items. If an item requires state clarification from an owner, pm-ai triggers FR-26 at least 1 hour before the meeting to send automated Teams/email inquiries to respective owners.  
    2. Exactly 15 minutes before the meeting, pm-ai aggregates active work items, blockers, candidate backlog items, commitment ledger status (commitments\_log.md), and participant activity across Git, Teams, Outlook, and recorded transcripts.  
    3. pm-ai renders the updated dashboard to \<project-root\>/.project-ai/memory/daily\_dashboard.md and delivers a push notification containing an interactive summary card to Andrei's Telegram bot and CLI console output.  
    4. Andrei reviews pre-filled participant activity summaries, blocker root causes, and unfulfilled commitment flags from commitments\_log.md.  
  * **Climax:** During the meeting, Andrei and the team bypass generic status updates and focus entirely on validated blockers and agenda resolutions.  
  * **Resolution:** Spoken commitments and target dates from today's meeting are captured into commitments\_log.md to serve as the baseline audit for future commitment validations.  
> * **UJ-7. Andrei and team leverage explicit verbal commands vs. implicit discussion extractions during and after meetings.**  
  * **Persona \+ context:** Andrei and the team in an architecture meeting.  
  * **Entry state:** Transcript ingestion pipeline active post-meeting.  
  * **Path:**  
    1. During the meeting, Andrei says explicitly: *"pm-ai, update WI-226 with changing requirement A to X and dispatch research on SQLite WAL performance."*  
    2. Later, team members discuss changing the cache eviction TTL from 60s to 300s for WI-108, without explicitly addressing pm-ai or John.  
    3. Post-meeting, pm-ai parses the transcript:  
       * **Explicit Action:** Immediately updates WI-226 requirement notes and dispatches the background research job.  
       * **Implicit Extraction:** Identifies the TTL discussion regarding WI-108, extracts target WI-108, owner, and priority, drafts proposed updates, and logs candidate commitment entries to commitments\_log.md.  
    4. pm-ai sends a Telegram/CLI Summary Card to Andrei containing:  
       * Explicit Confirmation Section detailing automatically executed commands.  
       * Interactive Approval Card for WI-108 update: \[WI-108: Update TTL to 300s\] with owner and priority flags.  
  * **Climax:** Andrei approves the implicit WI update via 1-tap Telegram button or CLI command (pm-ai approve WI-108); pm-ai commits the change to GitLab WI-108.  
  * **Resolution:** Explicit directives execute instantly; implicit discoveries remain safely staged until Andrei grants approval.  
> * **UJ-8. Andrei requests post-meeting transcript analysis for a missed optional meeting.**  
  * **Persona \+ context:** Andrei was double-booked and missed an optional technical sync on Payment Gateway integration, but the meeting was recorded in Teams.  
  * **Entry state:** Meeting finishes; Andrei opens Telegram or CLI prompt.  
  * **Path:**  
    1. Andrei sends a voice or text command: *"Fetch the transcript for today's Payment Gateway Sync and run post-meeting analysis."*  
    2. pm-ai locates the Teams meeting recording/transcript via Calendar integration, downloads the transcript stream, and processes it through the extraction pipeline within 600 seconds.  
    3. pm-ai outputs a Summary Card detailing explicit requests executed, implicit WI/doc updates (with parsed/suggested WI numbers, owners, and priorities), key architectural decisions, and interactive approval prompts.  
  * **Climax:** Andrei gets full decision clarity and approves implicit updates in 30 seconds without listening to a 45-minute recording.  
  * **Resolution:** The missed meeting's outputs are indexed in .project-ai/memory/event\_log.md and commitments\_log.md, staged for tomorrow's standup validation.  
> * **UJ-9. Andrei conducts mindful weekly and daily focus planning across goal horizons and burnout limits.**  
  * **Persona \+ context:** Andrei (Engineering PM) conducting his Monday morning weekly alignment and daily focus calibration to balance operational triage against mid-term sprint milestones and long-term career growth.  
  * **Entry state:** Opens Telegram or terminal prompt (pm-ai plan or interactive REPL) on Monday at 07:30 AM.  
  * **Path:**  
    1. pm-ai synthesizes daily\_dashboard.md by cross-referencing upcoming Outlook calendar load, active GitLab Work Items, pending commitments in commitments\_log.md, and 3-tier targets in strategic\_goals.md.  
    2. pm-ai evaluates the Anti-Burnout Telemetry Shield (FR-16). Detecting 26 hours of scheduled meetings and 3 consecutive late-evening commit reviews from the previous week, pm-ai issues an early warning: *"⚠️ High Burnout Density Alert: Calendar commitment is at 65% capacity. Operational firefighting risk is critical."*  
    3. pm-ai presents a structured planning breakdown across three horizons:  
       * **Strategic (Long-Term):** Allocate 4 hours for Q3 Auth Architecture refactor specs.  
       * **Tactical (Middle-Term):** Ensure Alex and Laura deliver Redis benchmark validation for Project Alpha.  
       * **Operational (Short-Term):** Triage 5 open GitLab PR reviews and clear email/Teams backlog.  
    4. pm-ai explicitly proposes declining two optional syncs and scheduling two 2-hour uninterrupted focus blocks on Tuesday and Thursday.  
    5. Andrei taps \[Accept Schedule & Block Focus\] on Telegram or issues pm-ai plan apply in CLI, making minor adjustments to the focus block start times.  
  * **Climax:** pm-ai updates daily\_dashboard.md, writes focus blocks to Outlook Calendar, and updates local strategic alignment metrics, protecting Andrei's cognitive bandwidth before the week's operational noise begins.  
  * **Resolution:** Andrei enters the week with strict work boundaries, explicit goal alignment across all three horizons, and proactive burnout guardrails in place.  
> * **UJ-10. Andrei configures and expands external system telemetry connectors via CLI or Telegram.**  
  * **Persona \+ context:** Andrei needs to configure connection credentials for existing services (GitLab, Teams, Outlook, HR tools) or add new external platforms (e.g., Jira, Slack, Notion) into pm-ai to expand telemetry coverage without restarting core daemons or exposing raw secrets.  
  * **Entry state:** Opens terminal (pm-ai connector) or sends Telegram command (/connectors).  
  * **Path:**  
    1. Andrei inputs /connectors add jira on Telegram or runs pm-ai connector add \--type jira in CLI.  
    2. pm-ai displays a secure step-by-step prompt requesting target domain URL, API token/OAuth key, and sync parameters.  
    3. pm-ai executes an immediate endpoint health check probe to verify API connectivity, permissions, and webhook endpoints.  
    4. Upon successful probe verification, pm-ai encrypts credentials inside \~/.manager-ai-private/config.json with file permissions 600 and dynamically registers the Jira harvester module into the active background radar without requiring a daemon restart.  
    5. pm-ai triggers a background historical telemetry backfill (past 7 days) and outputs a confirmation card displaying active status, connector health, and available entity mappings (e.g., Jira Issues ![][image2] Work Items).  
  * **Climax:** pm-ai seamlessly incorporates Jira tickets, Slack discussions, or Notion docs into morning dashboards, 1:1 dossiers, and deep inquiry queries alongside existing GitLab and Teams telemetry.  
  * **Resolution:** Andrei manages and expands his multi-tool ecosystem across both personal and project scopes in under 2 minutes with zero plaintext secret exposure.

## **3\. Glossary**

> * **Sovereign Personal Scope (\~/.manager-ai/):** The local directory containing personal career records, private reflections, and strategic coaching telemetry that is never committed to project repositories.  
> * **Isolated Project Scope (\<project-root\>/.project-ai/):** The repository-specific directory committed to version control, containing project-specific rules, task scripts, and team-facing personas.  
> * **External System Connector:** A modular plugin component within pm-ai that interfaces with external APIs (e.g., GitLab, Teams, Outlook, HR MCP, Slack, Jira, Notion) to harvest telemetry, sync state, and post responses using encrypted credential storage.  
> * **Connector Schema:** A standardized data contract and event normalization protocol that converts disparate external system activity (commits, tickets, channel chats, pages) into unified JSON telemetry entries inside event\_telemetry.db and event\_log.md.  
> * **CLI Interactive REPL Shell:** A terminal-based interactive shell started by running pm-ai without parameters, allowing the PM to type fixed commands or open natural language prompts continuously until explicitly typing exit or quit.  
> * **Socratic 1:1 Protocol:** An asynchronous or conversational dialogue mechanism conducted via Telegram or CLI where pm-ai surfaces telemetry-backed blind spots and asks reflective questions rather than issuing direct mandates.  
> * **High-Context Voice Concierge:** The capability of pm-ai to expand short voice prompts into detailed, context-rich correspondence by synthesizing background repository specs, meeting transcripts, and project data.  
> * **Contextual Web & Literature Engine (FR-17):** The background ingestion and situational matching of external industry RSS feeds and HTTP web pages against live project bottlenecks, team dynamics, and career goals.  
> * **Meeting ROI Metric:** A post-meeting mindfulness calculation (![][image3]) displayed as an informative metric within post-meeting summary header blocks to foster team cost awareness.  
> * **Verbal Commitment Sync:** The automatic extraction of spoken meeting promises and staging of timestamped comments attached to target GitLab Work Items.  
> * **Meeting Commitment Ledger & Lifecycle:** The persistent storage mechanism (recorded as structured Markdown entries in .project-ai/memory/commitments\_log.md and indexed in event\_telemetry.db) that captures extracted spoken promises, assigned owners, target deadlines, target Work Items, lifecycle statuses (\[STAGED\_APPROVAL\], \[PENDING\], \[FULFILLED\], \[ALTERED\], \[BROKEN\]), and telemetry verification evidence.  
> * **Spoken Anchor Protocol & Fuzzy Recovery:** A structured speaking convention used to identify target Work Item numbers, coupled with an automated fuzzy search recovery mechanism (\>85% confidence threshold) for phonetic or transcript speech recognition errors.  
> * **Explicit In-Meeting Command:** A direct spoken directive during a meeting explicitly addressing the assistant by name (e.g., *"pm-ai, update WI-226..."*) that serves as explicit authorization for immediate downstream execution without requiring staged confirmation cards.  
> * **Implicit Discussion Extraction:** Information, context, decisions, or ticket updates derived from general team meeting conversations where pm-ai was not explicitly invoked.  
> * **Interactive Approval Card:** A structured Telegram notification card or CLI interactive prompt displaying proposed implicit updates (Work Items with parsed/suggested numbers, owners, priorities, documentation, decisions) with \[Approve\] and \[Edit\] action options requiring PM confirmation before external state mutation.  
> * **Asynchronous Missed Meeting Ingestion:** On-demand retrieval and analysis of recorded Teams/Outlook meeting transcripts for sessions where the PM was absent or listed as optional.  
> * **Transcript-Triggered Research Task:** An asynchronous research job spawned by an in-meeting command requiring multi-source synthesis, web research, or documentation lookup, executed directly and delivered via email or Work Item comments.  
> * **Career Dossier:** A pre-meeting executive summary combining recent Git telemetry, custom monitored metrics, and external HR goal tracking pushed via Telegram or CLI prior to employee 1:1 syncs.  
> * **Meta-Coaching Scorecard:** A post-session evaluation mechanism capturing Coaching Efficiency (1-10) and Domain Distress (1-10) scores to calibrate persona questioning strategies.  
> * **Anti-Burnout Telemetry Shield:** Passive monitoring of working hours, calendar density, and PTO usage to proactively surface workload exhaustion risks strictly inside 1:1 coaching dialogues and weekly/daily planning workflows.  
> * **Unified Telemetry & Decision Log Store:** The consolidated storage mechanism recording all operational events, system actions, and leadership decisions as typed telemetry JSON entries inside event\_log.md and event\_telemetry.db.  
> * **Asynchronous Deep Inquiry Engine:** System capability permitting complex multi-source telemetry queries (commits, CI/CD, calendar, transcripts) with non-blocking deferred delivery of structured results over Telegram or CLI.  
> * **Documentation Drift Check:** Automated comparison between recent meeting decisions/transcripts and committed repository Markdown documentation to detect protocol or specification mismatches.  
> * **Pre-Meeting Preparation Dashboard:** A pre-meeting synthesized view combining active work items, blocked items, backlog priorities, multi-day trend analysis, and per-participant cross-source activity research generated prior to scheduled meetings.  
> * **Daily Commitment Validation:** Automated verification comparing promises made during previous meeting transcripts against harvested Git, Teams, Outlook, and Work Item evidence.  
> * **Transcript Lifecycle Policy:** The configured retention duration for raw text meeting transcript logs (default 30 days, user-configurable), after which raw transcripts are automatically purged to conserve disk space once extracted into persistent Markdown summaries, decisions, and Work Items.

## **4\. Features & Requirements Catalog**

### **4.1 Low-Friction Telemetry Ingestion & Universal Glue**

#### **FR-01: Structured Speaking Protocol, Transcript Analysis & Fuzzy Recovery**

Parse meeting transcripts for deterministic spoken keywords (e.g., ticket IDs, assignees) and match them against GitLab Work Item IDs.  
In cases where no matching Work Item ID exists or speech recognition misinterprets the spoken reference:

> 1. System shall execute fuzzy search matching and contextual reference recovery against mentions earlier or later in the transcript. If recovered with a confidence score ![][image4], proceed with updating the Work Item as planned.  
> 2. If recovery fails (![][image5] confidence), log a \[UNMATCHED\_ANCHOR\] token and surface the topic in the post-meeting summary approval section. The PM shall be presented with candidate matching Work Items to select from or the option to enter a Work Item ID manually. Realizes UJ-3, UJ-6, UJ-7, UJ-8.

**Consequences (testable):**

> * Given a spoken reference misrecognized as "WI-2260" when only "WI-226" exists in active memory, fuzzy context recovery maps the ID to WI-226 with ![][image4] confidence and updates the ticket within SLA.  
> * Given an unresolvable reference with ![][image6] confidence, the system stages an \[UNMATCHED\_ANCHOR\] prompt in the post-meeting summary card displaying a dropdown/list of candidate WIs and a manual entry field.

#### **FR-02: 24/7 Passive Context Telemetry Radar**

Background daemon harvesting telemetry across configured external system connectors (GitLab, Teams, Outlook calendars, emails, Jira, Slack, Notion) every 4 hours into local Markdown cache and SQLite index. Realizes UJ-1, UJ-2, UJ-5, UJ-6, UJ-9, UJ-10.  
**Consequences (testable):**

> * Executes background harvesting cycle every 240 minutes (![][image7] minutes); writes raw parsed diffs to \~/.manager-ai-private/event\_telemetry.db without exceeding 50MB RSS memory footprint during execution.  
> * If an external provider API (e.g., GitLab, Teams, or Jira) returns an HTTP 5xx error or times out, the daemon logs the failure to event\_log.md and retries with exponential backoff without crashing the runner.

#### **FR-03: Calendar Event-Driven Processing, On-Demand Missed Meeting Analysis & Cost Metrics**

Automatically fetch and process meeting transcripts upon completion or upon explicit PM request for missed/optional meetings within 600 seconds (10 minutes). Calculate post-meeting Man-Hour Cost (![][image3]) and include this metric in the post-meeting summary card header (as a secondary informative metric). Realizes UJ-3, UJ-6, UJ-7, UJ-8.  
**Consequences (testable):**

> * Upon receipt of an Outlook Calendar meeting.ended trigger or on-demand PM command, fetching and parsing pipelines complete within 600 seconds.  
> * Post-meeting summary cards attach exact Man-Hour Cost calculations (![][image8]) inside the card header block.

#### **FR-04: Resilient Background Runner & Offline Buffer**

Asynchronous micro-job pipeline with exponential backoff, retry logic, and local SQLite offline buffering for uninterrupted offline operation.  
**Consequences (testable):**

> * When network connectivity is severed (simulated offline mode), outgoing API actions (e.g., GitLab or Jira comment posts) buffer strictly in event\_telemetry.db with state PENDING\_RETRY.  
> * Upon network restoration, buffered operations replay sequentially in chronological order within 30 seconds of link re-establishment.

#### **FR-05: Spoken Anchor Extraction & Direct In-Meeting Commands**

Detect explicit invocation tokens (e.g., pm-ai, John) and Spoken Anchor Protocol patterns in raw meeting transcripts. Extract target Work Item IDs, requirement edits, assignee changes, and status transitions, updating GitLab Work Items and local memory within SLA. Explicit commands serve as direct authorization for state updates. Post-meeting summary reports shall explicitly include an Automatic Execution Section confirming all executed direct requests. Realizes UJ-3, UJ-7.  
**Consequences (testable):**

> * Given a transcript segment: *"pm-ai, update WI-226 requirement A to X"*, the system directly updates WI-226 notes and logs the action under \[AUTHORIZATION: EXPLICIT\_VERBAL\].  
> * Post-meeting summary cards explicitly list a section titled \#\# Automatically Executed Commands detailing each direct request and its execution status token.

#### **FR-06: Dual-Authorization Meeting Extraction, Commitment Sync & Interactive Approval Engine**

Parse both explicit directives and implicit team discussions from meeting transcripts. Automatically execute explicit commands while staging implicit Work Item updates, commitment syncs, documentation revisions, and decision logs.  
For implicit discussions, the extraction pipeline must parse or suggest:

> 1. Target Work Item ID (existing ID or recommendation to create a new Work Item).  
> 2. Responsible person / assignee.  
> 3. Priority level and relevant metadata flags.  
>    Deliver a Telegram Summary Card or CLI Approval Queue to the PM containing executed explicit actions, synthesized meeting context, and interactive approval controls (\[Approve\], \[Edit\]) for implicit updates. Staged commitments are logged into .project-ai/memory/commitments\_log.md with status \[STAGED\_APPROVAL\]. Realizes UJ-3, UJ-6, UJ-7, UJ-8.

**Consequences (testable):**

> * Implicit discussion regarding cache TTL generates an Interactive Approval Card proposing an update to WI-108, assigning Alex, setting priority High, with \[Approve\] and \[Edit\] buttons.  
> * Spoken commitments (e.g., *"Alex: I will deliver benchmarks by Thursday"*) stage as candidate comments attached to target Work Items and write a \[STAGED\_APPROVAL\] entry in commitments\_log.md requiring PM approval before posting.

#### **FR-07: In-Band Invocations, Fact-Checking & Transcript-Triggered Research Execution**

Detect verbal invocations using the persona name (pm-ai, John) during meetings to execute in-band direct commands, conduct post-meeting verbal fact-checking digests against project documentation, and parse complex verbal research requests. Direct research commands execute asynchronously without prompt staging, routing synthesized findings to designated outputs (email follow-up to attendees or comments attached to specific Work Items). Realizes UJ-7.  
**Consequences (testable):**

> * Statements containing persona name and factual claims generate a \[FACT\_CHECK\_DIGEST\] block in the post-meeting summary.  
> * Spoken research command *"pm-ai, dispatch research on SQLite WAL performance and post to WI-102"* triggers background web search, attaching synthesized Markdown reports to GitLab WI-102 and emailing attendees within 15 minutes of meeting conclusion.

#### **FR-08: On-Demand Missed Meeting Ingestion & Analysis**

Accept natural language commands via Telegram or CLI (voice or text) specifying a past or missed calendar meeting, download the available Teams transcript/recording stream, and execute the full FR-06 dual-authorization summary and extraction pipeline. Realizes UJ-8.  
**Consequences (testable):**

> * Given user prompt *"Fetch transcript for today's Payment Gateway Sync"*, system locates the matching Teams recording asset ID, downloads raw text, and renders the FR-06 Summary Card within 300 seconds.  
> * If no transcript asset is found, system returns an error card explaining missing recording permissions within 15 seconds.

#### **FR-35: Extensible External System Connector Framework & Dynamic CLI/Telegram Management**

Provide an extensible connector architecture and interactive management interfaces via CLI (pm-ai connector) and Telegram (/connectors) allowing the PM to view, test, enable, disable, and configure external telemetry and data sync sources (GitLab, Teams, Outlook, HR platforms, Slack, Jira, Notion, and custom OpenAPI/webhook integrations).

> 1. **Dynamic Configuration & Health Probe:** Invoking connector configuration prompts for domain endpoints, authentication tokens, or OAuth keys, executes a synchronous connection health check probe within 10 seconds, and writes encrypted credentials to \~/.manager-ai-private/config.json with 600 file permissions per NFR-08.  
> 2. **Modular Event Normalization:** Every external system connector must map raw external entity events (e.g., Jira issue edits, Slack channel messages, Notion page updates, GitLab MRs) into standardized Connector Schema JSON events ingested by event\_telemetry.db and indexed for event\_log.md.  
> 3. **Hot Plugin Loading:** Adding or updating a connector module takes effect dynamically in the passive telemetry radar (FR-02) without requiring a daemon restart. Realizes UJ-10.

**Consequences (testable):**

> * Executing pm-ai connector add \--type jira \--domain company.atlassian.net prompts for API token input, performs a live API health check within 10 seconds, and appends the validated config to config.json with encrypted storage.  
> * Disabling a connector (pm-ai connector disable slack) immediately halts background polling for that connector without interrupting other active harvesters.  
> * Ingested events from newly added connectors (e.g., Jira ticket updates or Slack messages) appear in deep inquiry results (FR-23) within 60 seconds of harvest.

### **4.2 Executive Coaching & Socratic Strategy**

#### **FR-09: Multi-Tier Strategic Focus Briefing Engine**

Pre-render daily briefings (\~/.manager-ai/memory/daily\_dashboard.md) categorizing Time-Critical Activities, Proactive Enablement, 3-Tier Strategic Milestones, and Leadership Notes. Realizes UJ-1, UJ-9.  
**Consequences (testable):**

> * Daily focus briefing file \~/.manager-ai/memory/daily\_dashboard.md is generated by 07:00 AM local time daily.  
> * Markdown file contains strictly formatted 4-tier headers (\#\# Time-Critical Activities, \#\# Proactive Enablement, \#\# 3-Tier Strategic Milestones, \#\# Leadership Notes) with no unpopulated empty sections.

#### **FR-10: Traceable Event Log & Self-Retrospective Engine**

Immutably log all decisions, operational events, and telemetry diffs as typed entries in event\_log.md and local SQLite event\_telemetry.db. Compute weekly pm-ai Performance Index. Realizes UJ-1, UJ-9.  
**Consequences (testable):**

> * Every state mutation appends an immutable JSON line to \~/.manager-ai/memory/event\_log.md with ISO-8601 timestamp, actor ID, and action category.  
> * Running pm-ai retrospective \--weekly aggregates weekly action counts and outputs a calculated Performance Index score (![][image9]).

#### **FR-11: Micro-Decision Daily Alignment Engine**

Evaluate daily tasks against short-, medium-, and long-term goal frameworks and attach concise "Strategic Rationale Snippets" to recommendations. Realizes UJ-9.  
**Consequences (testable):**

> * Every task recommendation rendered on the Daily Focus Briefing includes a \[Strategic Alignment: \<Tier\>\] tag mapping directly to a goal defined in \~/.manager-ai/memory/strategic\_goals.md.

#### **FR-12: Socratic 1:1 Strategic Coaching Protocol**

Conduct interactive 1:1 coaching dialogues via Telegram or CLI console session, starting with a telemetry review, surfacing blind spots, and asking reflective Socratic questions. Realizes UJ-1.  
**Consequences (testable):**

> * Initiating a 1:1 session (pm-ai console 1on1 or Telegram /1on1) surfaces a time-allocation breakdown comparing actual telemetry against targets in strategic\_goals.md in the first message turn.  
> * System frames responses as open-ended questions ending in question marks (![][image10] of turns) rather than direct prescriptive directives.

#### **FR-13: Executive Sovereignty & Controlled Notification Boundaries**

Maintain strict work-hour quiet boundaries by silencing unsolicited alerts and background telemetry interruptions during deep work hours. Scheduled push notifications are explicitly permitted for delivering pre-meeting analysis briefings (FR-32, 15m/1h prior) and post-meeting summary/approval cards (FR-06). Realizes UJ-1, UJ-3, UJ-6, UJ-9.  
**Consequences (testable):**

> * Background telemetry harvesters emit zero unsolicited active desktop/mobile push notifications during active work hours.  
> * Push notifications are strictly restricted to scheduled pre-meeting preparation alerts and post-meeting summary cards.

#### **FR-14: Bi-Directional Meta-Coaching & Scorecard Engine**

Capture post-1:1 feedback on Coaching Efficiency (1-10 numeric rating scale) and Domain Distress (1-10 numeric rating scale) to calibrate persona questioning strategies. Realizes UJ-1.  
**Consequences (testable):**

> * Upon 1:1 session conclusion, system prompts for two numeric ratings on a scale of 1 to 10\.  
> * Ratings are stored in coaching\_1on1\_history.md and dynamically alter persona parameters for subsequent sessions.

#### **FR-15: Continuous Leadership Auditing & Guided Experiments**

Silently audit leadership dynamics to surface blind spots and guide the PM through real-world behavioral experiments during 1:1s. Realizes UJ-1.  
**Consequences (testable):**

> * When telemetry shows a specific activity repeated ![][image11] consecutive sprints, system proposes a structured 1-sprint behavioral delegation experiment in the next 1:1 summary.

#### **FR-16: Sovereign Personal Scope & Anti-Burnout Shield**

Maintain independent career directory (\~/.manager-ai/) and evaluate working hours, calendar density, and PTO balances to detect burnout risks inside 1:1 dialogues and weekly/daily planning sessions. Realizes UJ-1, UJ-9.  
**Consequences (testable):**

> * If harvested telemetry indicates ![][image12] hours daily calendar/commit activity for 3 consecutive days or calendar density ![][image13], system flags an \[ELEVATED\_WORKLOAD\_ALERT\] inside private 1:1 coaching logs and planning briefings.  
> * Anti-burnout indicators are strictly excluded from all public or project-level files in \<project-root\>/.project-ai/.

#### **FR-17: Contextual Web & Literature Recommendation Engine**

Continuously monitor and digest RSS feeds and arbitrary HTTP web pages configured in article\_sources.md. Dynamically cite relevant publications and web articles during 1:1 coaching sessions or Daily Briefings. Realizes UJ-1.  
**Consequences (testable):**

> * Background job polls configured RSS feeds and HTTP web pages in article\_sources.md every 24 hours (![][image14] minutes), creating vector embeddings for updated content.  
> * Cites at most 3 situational articles/web pages per week across all briefings/1:1s, requiring exact URL and title matches from article\_sources.md.

### **4.3 Mobile Command & CLI Access Interfaces**

#### **FR-18: Terminal-Native Interactive CLI Console**

Provide a command-line executable (pm-ai). Running pm-ai without parameters launches an interactive REPL console session where the PM can type fixed subcommands or open natural language prompts continuously until explicitly typing exit or quit. Full feature parity with the Telegram text interface is maintained. Realizes UJ-1, UJ-4, UJ-5, UJ-6, UJ-8, UJ-9, UJ-10.  
**Consequences (testable):**

> * Executing pm-ai without arguments opens an interactive REPL shell prompt (pm-ai\> ) within 1.0 second.  
> * Entering commands or open prompts processes responses in stdin/stdout until exit or quit is entered.

#### **FR-19: Telegram Mobile Command Bridge & Voice Concierge Interface**

Primary mobile UI for daily briefings, voice note triage, Git/task action dispatches, interactive approval cards, weekly focus planning, connector setup (/connectors), and 1:1 coaching sessions over Telegram HTTPS webhook/polling. Realizes UJ-1, UJ-2, UJ-4, UJ-5, UJ-6, UJ-7, UJ-8, UJ-9, UJ-10.  
**Consequences (testable):**

> * Telegram webhook endpoint responds to incoming Telegram API updates with HTTP 200 within 2000ms.  
> * Toggling approval buttons on interactive cards updates inline button text state upon execution.

#### **FR-20: Dynamic Persona & Communication Trait Engine**

Dynamically loaded persona profiles (persona.md) defining assistant tone, directness, and constructiveness levels across CLI and Telegram outputs. Persona configuration can be modified directly via CLI subcommands (e.g., pm-ai persona set directness=concise) or Telegram commands.  
**Consequences (testable):**

> * Executing pm-ai persona set directness=concise via CLI or Telegram immediately updates persona.md parameters and alters downstream response formatting without restarting the daemon.

#### **FR-21: Voice/Text Context-Enriched Response Synthesis**

Synthesize concise voice or text instructions into detailed, context-grounded response drafts across communication channels (Teams, Email, Slack), reviewed one by one before dispatch. Realizes UJ-2.  
**Consequences (testable):**

> * Given a 20-second voice note, system synthesizes distinct draft cards detailing target channel, recipient name, full body text, and cited source artifacts.  
> * Drafts remain in STAGED state and are never dispatched externally until explicit approval (\[Send\]) is registered via CLI or Telegram.

#### **FR-22: Audio & CLI Git Notification Dispatcher**

Allow the PM to dispatch code check requests, review comments, or ticket status updates via Telegram voice/text commands or CLI subcommands.  
**Consequences (testable):**

> * Command pm-ai dispatch \--ticket WI-102 \--comment "Approved" posts the comment to GitLab WI-102 within 10 seconds and outputs confirmation hash to CLI/Telegram.

#### **FR-23: Multi-Source Asynchronous Activity & Decision Inquiry Engine**

Process complex natural language text or voice queries (via CLI or Telegram) requesting time-bound activity breakdowns for specific team members across all connected platforms (GitLab, Teams, Outlook, Jira, Slack, Notion) and return a categorized, synthesized summary card. Realizes UJ-5.  
**Consequences (testable):**

> * Query *"What activity did Alex do yesterday?"* returns a structured card grouping activity by Git Commits, MRs, WI/Jira Edits, Slack Discussions, and Meetings within 60 seconds.  
> * Every activity item cited contains an exact URL link, commit SHA, or ticket anchor reference.

#### **FR-24: Knowledge Repository & Operational Guide Query**

Provide immediate retrieval of project architectural guidelines, AWS/DevOps environment commands, and internal team processes stored across local .project-ai/rules/ and memory markdown files (or connected Notion pages) upon user request via CLI or Telegram. Realizes UJ-5.  
**Consequences (testable):**

> * Querying CLI/Telegram for an operational procedure defined in .project-ai/rules/engineering\_specs.md returns exact command blocks within 15 seconds.

#### **FR-25: Specification-to-Discussion Drift Auditor**

Query and compare recent meeting discussions or transcript decisions against committed Markdown documentation (e.g., engineering\_specs.md), flagging explicit mismatches, missing updates, or drift. Realizes UJ-5.  
**Consequences (testable):**

> * If transcript records an agreed decision to change API port to 8080, but engineering\_specs.md specifies 8000, running drift check surfaces an \[EXPLICIT\_SPEC\_DRIFT\] alert citing file line number and transcript timestamp.

### **4.4 Meeting Optimization & Team Career Cultivation**

#### **FR-26: Pre-Meeting Automated Inquiry Proxy**

Automatically scan agendas of scheduled meetings (e.g., daily standups, architecture syncs, planning) during pre-meeting research preparation. If an agenda item requires state clarification, pm-ai shall trigger at least 1 hour prior to the meeting to issue targeted automated inquiries to respective item owners via Teams direct messages, Slack, or email. Realizes UJ-6.  
**Consequences (testable):**

> * For a scheduled meeting with an unverified item on WI-108, pm-ai dispatches an automated clarification inquiry to the owner at least 60 minutes prior to meeting start.  
> * Owner responses received prior to the 15-minute preparation window are automatically integrated into the pre-meeting dashboard card.

#### **FR-27: Unified Telemetry & Decision Log Store**

Store all system decisions, architectural choices, meeting outcomes, and harvested telemetry events as structured, typed JSON log lines inside event\_log.md and SQLite event\_telemetry.db across both manager (\~/.manager-ai/memory/) and project (.project-ai/memory/) scopes. Realizes UJ-1, UJ-9.  
**Consequences (testable):**

> * Decisions recorded during meetings, 1:1 sessions, or weekly focus planning write directly to event\_log.md with category tag \[TYPE: DECISION\].  
> * Semantic query across past decisions retrieves historical decision context within 5 seconds.

#### **FR-28: Autonomous AI Work Item Execution Engine**

Direct execution of simple documentation, coding, or routing tasks assigned to pm-ai in GitLab or Jira, producing ready-to-merge Merge Requests or PRs.  
**Consequences (testable):**

> * Assigning a GitLab Work Item or Jira issue with label pm-ai:execute triggers autonomous generation of a git branch, Markdown/code edit, and Merge Request within 300 seconds.

#### **FR-29: MCP Presentation, Spreadsheet & Universal Tool Glue**

Use Model Context Protocol (MCP) integrations to generate slide presentations, update spreadsheets, and sync disconnected platform data.  
**Consequences (testable):**

> * Executing an MCP skill invocation successfully transmits payload adhering to MCP JSON-RPC protocol specification.

#### **FR-30: Cohort & Individual Metric Performance Monitor**

Allow the PM to define custom tracking metrics (e.g., number of issues found per MR review, questions asked per standup) for specific team members or cohorts over configurable monitoring durations (1 month, quarter, year, indefinitely). Aggregated metric trends and dynamics are available on-demand via CLI/Telegram and automatically incorporated into employee dossiers for 1:1s and performance reviews. Realizes UJ-4.  
**Consequences (testable):**

> * Querying performance for a team member returns specified custom metric values alongside historical trend dynamics over the defined monitoring interval.  
> * Generated 1:1 Career Dossiers automatically embed active metric tracking blocks for the subject team member.

#### **FR-31: Multi-HR Tool MCP Integration & Career Dossier Pipeline**

Connect to external HR platforms (e.g., Lattice, 15Five, custom spreadsheets) via modular MCP connectors to synthesize pre-meeting Career Dossiers. Post-1:1 agreed goals and performance objectives extracted automatically from meeting transcripts require Andrei's explicit approval before syncing back to HR platforms. Support encrypted credential storage in config.json. Realizes UJ-4.  
**Consequences (testable):**

> * 15 minutes prior to a calendar 1:1 event, system executes HR MCP connector and pushes a Career Dossier card.  
> * Extracted post-1:1 goals remain in STAGED\_APPROVAL state until Andrei explicitly approves them via CLI/Telegram, triggering MCP sync to the HR API.

#### **FR-32: Telemetry-Enriched Pre-Meeting Preparation Dashboard Generator**

Synthesize pre-meeting dashboards for scheduled meetings (daily standups, architecture reviews, planning). Execute background research across Git, Teams, Outlook, Jira, Slack, and recorded transcripts tailored to meeting type, agenda, and attendees. Trigger generation 15 minutes prior to the meeting (or at least 1 hour prior if FR-26 inquiries are required), writing Markdown output to \<project-root\>/.project-ai/memory/daily\_dashboard.md and pushing interactive summary cards to Telegram and CLI. Realizes UJ-6.  
**Consequences (testable):**

> * Preparation dashboards generate 15 minutes prior to meeting start time (or 60 minutes prior if automated inquiries are needed).  
> * Interactive preparation summary cards deliver to Telegram and CLI within 30 seconds of dashboard generation.

#### **FR-33: Previous Meeting Commitment Validation & Trend Auditor**

Retrieve active and historical commitments from the persistent Meeting Commitment Ledger (.project-ai/memory/commitments\_log.md / event\_telemetry.db), compare spoken promises against harvested cross-platform telemetry (Git MRs, commits, Work Item / Jira status, Teams/Slack activity), and explicitly highlight met, altered, or broken promises along with multi-day stalled blockers on the pre-meeting preparation dashboard. Realizes UJ-6.  
**Consequences (testable):**

> * Spoken promise stored in commitments\_log.md is evaluated against harvested Git telemetry prior to a meeting; unfulfilled promises past their target date are tagged \[UNFULFILLED\_COMMITMENT\] on today's pre-meeting preparation card.  
> * Commitments whose status changed to \[FULFILLED\] via verified MR telemetry are displayed under the \#\# Met Commitments subsection.

#### **FR-34: Spoken Commitment Persistence, Lifecycle Management & Ledger**

Extract, persist, and maintain the lifecycle of all spoken meeting commitments and promises in local persistent storage (.project-ai/memory/commitments\_log.md and indexed in event\_telemetry.db).

> 1. **Extraction & Initial Staging:** Upon processing meeting transcripts (FR-03, FR-06), extracted commitments are appended to .project-ai/memory/commitments\_log.md as structured Markdown entries and indexed in event\_telemetry.db. Each entry records: commitment\_id, timestamp, speaker, target\_assignee, description, target\_work\_item, due\_date, and initial status (\[STAGED\_APPROVAL\] or \[PENDING\]).  
> 2. **Lifecycle Transitions:** Automatically evaluate and update commitment status based on cross-platform telemetry or direct PM triage:  
   * \[STAGED\_APPROVAL\]: Spoken implicit commitment awaiting PM approval via Telegram/CLI card. Upon PM approval, transitions to \[PENDING\].  
   * \[PENDING\]: Active commitment awaiting fulfillment before specified due date.  
   * \[FULFILLED\]: Telemetry confirms matching deliverable completed (e.g., merged MR, closed Work Item) on or before due date.  
   * \[ALTERED\]: Scope or due date updated in a subsequent meeting transcript or via direct PM edit.  
   * \[BROKEN\]: Target due date passed without confirming telemetry evidence or Work Item completion.  
> 3. **Maintenance & Auditability:** Maintain an append-only audit trail inside commitments\_log.md with verification hashes, event timestamps, and evidence references for retrospective auditing. Realizes UJ-3, UJ-6, UJ-7, UJ-8.

**Consequences (testable):**

> * Approved spoken commitment creates a valid, structured Markdown entry in .project-ai/memory/commitments\_log.md and SQLite row in event\_telemetry.db with state \[PENDING\].  
> * When telemetry detects a merged MR referencing the target Work Item of a \[PENDING\] commitment, system automatically updates commitment status to \[FULFILLED\] and appends the commit SHA verification reference.

## **5\. Non-Goals (Explicit)**

> * **No Real-Time Audio Interruption:** pm-ai does not speak live during meetings or interrupt speakers in real-time; transcript analysis and execution occur asynchronously post-meeting or via stream processing.  
> * **No Unsanctioned Autonomous External Writes for Implicit Extractions:** pm-ai will not modify external GitLab Work Items, Jira tickets, or project documentation based on implicit meeting discussions without explicit PM approval via Interactive Approval Cards or CLI approval commands. Spoken in-meeting directives explicitly addressing pm-ai or John serve as authorization.  
> * **No Unsolicited Mid-Work Interruptions:** pm-ai will not send unprompted notifications or message relays during active work hours. Push notifications are strictly bounded to scheduled pre-meeting prep cards (15m/1h prior) and post-meeting summary/approval reports.  
> * **No Public Anti-Burnout Alarms:** Workload and burnout indicators will not appear on project dashboards or team channels.  
> * **No Cloud Vector DB / Heavy SaaS RAG:** The system will not depend on cloud-hosted vector databases or SaaS RAG infrastructure.  
> * **No Pre-Merge Doc Gatekeeping:** Developer Merge Requests will never be blocked by documentation drift checks.

## **6\. System Architecture & Model Topology**

\+------------------------------------+   \+------------------------------------+  
|       TELEGRAM BOT INTERFACE       |   |    LOCAL CLI CONSOLE INTERFACE     |  
| (Voice Notes, Focus Briefings,     |   | (Interactive REPL Shell, Sub-      |  
|  Interactive Approval Cards, 1:1s) |   |  commands, Open Queries, Triage)   |  
\+-----------------+------------------+   \+-----------------+------------------+  
                  | (HTTPS Webhook)                        | (Stdio / Terminal)  
\+-----------------v----------------------------------------v------------------+  
|                    LOCAL pm-ai DAEMON & CONCIERGE RUNNER                    |  
|                                                                               |  
|  \+---------------------------+   \+-----------------------------------------+  |  
|  | Dynamic System Connectors |   |           Local Fast Models             |  |  
|  | (GitLab, Teams, Outlook,  |   | (Whisper Voice, Ollama Parsing/Extract) |  |  
|  |  Slack, Jira, Notion, HR) |   |                                         |  |  
|  \+-------------+-------------+   \+--------------------+--------------------+  |  
|                |                                      |                       |  
|  \+-------------v--------------------------------------v--------------------+  |  
|  |                     Frontier LLM (Claude 3.5 Sonnet)                    |  |  
|  |   (Strategic 1:1 Synthesis, Focus Briefings, Research & Dynamic Diffs) |  |  
|  \+------------------------------------+------------------------------------+  |  
\+---------------------------------------|---------------------------------------+  
                                        | (Read / Write State)  
\+---------------------------------------v---------------------------------------+  
|                         FILE SYSTEM STORAGE CONTRACT                          |  
|                                                                               |  
|  \[\~/.manager-ai/\] (Sovereign PM Scope)   \[\<project\>/.project-ai/\] (Project)   |  
|  \- manager\_principles.md                 \- persona.md                         |  
|  \- strategic\_goals.md                    \- conventions.md                     |  
|  \- coaching\_1on1\_history.md              \- engineering\_specs.md               |  
|  \- article\_sources.md                    \- daily\_dashboard.md                 |  
|  \- daily\_dashboard.md                    \- commitments\_log.md (FR-34 Ledger) |  
|  \- event\_log.md (Unified Telemetry)      \- event\_log.md (Unified Telemetry)   |  
|                                                                               |  
|  \[\~/.manager-ai-private/\] (Gitignored)                                        |  
|  \- event\_telemetry.db (SQLite Queue, Telemetry & Commitments Index)           |  
|  \- config.json (Encrypted Tokens: GitLab, Teams, Telegram, HR MCP, Jira, etc.)|  
\+-------------------------------------------------------------------------------+

## **7\. Cross-Cutting Non-Functional Requirements (NFRs)**

### **7.1 Performance & Latency Budgets**

> * **NFR-01 (Voice Transcription SLA):** Voice notes under 30 seconds must be transcribed and parsed by the local Whisper pipeline within 10 seconds of receipt.  
> * **NFR-02 (End-to-End Voice Triage):** Full round-trip time from receiving a 20-second voice note to rendering individual, context-enriched draft review cards in Telegram or CLI must not exceed 45 seconds.  
> * **NFR-03 (Meeting Ingestion & Post-Processing SLA):** Meeting transcripts must be parsed, spoken anchors/commands extracted, Work Item state updated, and staged research tasks queued within 600 seconds (10 minutes) of meeting completion.  
> * **NFR-04 (Asynchronous Deep Inquiry & Daily Synthesis SLA):** Deep multi-source query responses (FR-23, FR-25) and pre-meeting Briefings (FR-32, FR-33) must complete generation and deliver results within 60 seconds of trigger.  
> * **NFR-05 (Transcript Research Execution SLA):** Transcript-triggered background research tasks (FR-07) must synthesize findings and dispatch email/Work Item follow-ups within 15 minutes of meeting conclusion.  
> * **NFR-06 (Missed Meeting On-Demand Processing SLA):** Download and full dual-authorization extraction for an on-demand requested meeting transcript (FR-08) must complete and render a Summary Card within 300 seconds (5 minutes) of PM invocation.

### **7.2 Security, Privacy & Data Sovereignty**

> * **NFR-07 (Scope Boundary Isolation):** Files in \~/.manager-ai/ must never be indexed into or committed to project repositories. Automated pre-commit hooks verify that .manager-ai-private/ is gitignored.  
> * **NFR-08 (Encrypted Local Token & Secret Storage):** Credentials, API keys, dynamic connector tokens (GitLab, Teams, Telegram, HR MCP, Slack, Jira, Notion) stored in \~/.manager-ai-private/config.json must be stored in encrypted form at rest with 600 file permissions.  
> * **NFR-09 (Transcript Lifecycle & Automated Purge):** Raw meeting transcript text files stored in \~/.manager-ai-private/chat\_history/ must be maintained for a default window of 30 days (configurable in config.json). The background runner will automatically purge raw text transcripts older than the retention threshold after verified conversion into Markdown summaries, Work Item updates, decision logs, and memory indexes.

### **7.3 Reliability, Offline Resilience & Hardware Constraints**

> * **NFR-10 (Offline Queueing & Sequential Replay):** In the event of network disruption, all incoming audio notes, CLI commands, and state actions must buffer in event\_telemetry.db and replay sequentially without data loss upon reconnection.  
> * **NFR-11 (Cache Loss Recovery):** Deletion of event\_telemetry.db or local vector caches must result in zero data loss, rebuilding entirely from plain Markdown source files (including commitments\_log.md and event\_log.md).  
> * **NFR-12 (Hardware Baseline Constraint):** Minimum supported hardware specification requires 16GB RAM minimum on Apple Silicon (M-series) or 8GB VRAM NVIDIA GPU (CUDA) to guarantee local Whisper voice parsing and local Ollama model execution within latency targets without swap thrashing.

### **7.4 Cost & Token Efficiency**

> * **NFR-13 (Monthly Cost Cap):** Total monthly LLM API operational cost must remain under $20/month by maximizing deterministic scripts and local models, reserving frontier API calls strictly for high-level synthesis and dynamic research execution.

## **8\. Success Metrics & Counter-Metrics**

### **8.1 Primary Success Metrics**

> * **SM-1 (Executive Bandwidth Reclaimed):** Weekly meeting hours reduced by ![][image15] through async inquiry proxies and pre-meeting relevance checks. Validates FR-26.  
> * **SM-2 (Voice Response Latency):** Sub-60-second end-to-end duration to turn a 20-second voice instruction into approved, dispatched multi-channel replies. Validates FR-19, FR-21.  
> * **SM-3 (Socratic Coaching Utility):** Post-1:1 Coaching Efficiency Score averaged across monthly retrospectives ![][image16]. Validates FR-12, FR-14.  
> * **SM-4 (Literature Relevance Rate):** Percentage of contextual literature recommendations rated "actionable/relevant" during 1:1s ![][image10]. Validates FR-17.  
> * **SM-5 (Cost Efficiency):** Monthly operational LLM API expense maintained strictly ![][image17]. Validates NFR-13.  
> * **SM-6 (Deep Inquiry & Meeting Preparation Accuracy):** Accuracy rate of multi-source telemetry, pre-meeting status validations, and documentation drift queries validated by the PM without requiring manual re-queries ![][image18]. Validates FR-23, FR-24, FR-25, FR-32, FR-33, FR-34.  
> * **SM-7 (Spoken Anchor & In-Meeting Command Execution Precision):** Percentage of spoken anchors (including fuzzy-recovered references) and direct verbal commands correctly parsed and mapped to target Work Items without manual correction ![][image19]. Validates FR-01, FR-05, FR-06, FR-07.  
> * **SM-8 (Implicit Update Approval Accuracy):** Percentage of implicit meeting updates staged in Telegram/CLI Interactive Approval Cards accepted by PM without complete rejection ![][image20]. Validates FR-06, FR-34.

### **8.2 Counter-Metrics (Do Not Optimize)**

> * **SM-C1 (Message Draft Volume):** Do not optimize for raw volume of generated drafts. Focus on draft acceptance rate without extensive manual edits (![][image4]). Counterbalances SM-2.  
> * **SM-C2 (Literature Push Frequency):** Do not optimize for number of articles recommended per week (cap at ![][image21] situational citations/week to avoid cognitive spam). Counterbalances SM-4.  
> * **SM-C3 (Coaching Session Frequency):** Do not force daily coaching prompts; respect PM-initiated cadences to avoid session fatigue. Counterbalances SM-3.

## **9\. Phased Execution Roadmap**

| Phase | Focus Areas | Addressed Requirements |
| :---- | :---- | :---- |
| **Phase 1: Core Foundation & Interface Bridges** | Sovereign directory contract, Telegram bot bridge, Terminal CLI interactive REPL (pm-ai), encrypted secrets, local Whisper voice transcription, Event Log, Meeting Commitment Ledger structure, GitLab transcript materialization, and basic Extensible Connector framework. | FR-01, FR-03, FR-04, FR-10, FR-16, FR-18, FR-19, FR-20, FR-34, FR-35, NFR-08, NFR-09, NFR-12 |
| **Phase 2: High-Context Concierge & Telemetry Radar** | 24/7 background telemetry radar, multi-tool connector expansion (Teams, Outlook, Slack, Jira, Notion), pre-meeting preparation dashboard synthesizer, commitment validation auditor, automated inquiry proxy, voice/text context-enriched response synthesis, deep inquiry engine, spec drift auditor, spoken anchor extraction & fuzzy recovery, dual-authorization meeting extraction, missed meeting ingestion, and transcript research execution. | FR-02, FR-05, FR-06, FR-07, FR-08, FR-21, FR-22, FR-23, FR-24, FR-25, FR-26, FR-27, FR-28, FR-32, FR-33, FR-34, FR-35 |
| **Phase 3: Socratic Coaching & Web/Literature Engine** | Socratic 1:1 coaching protocol, daily strategic focus briefings, contextual web & literature recommendation engine, and meeting cost metrics. | FR-09, FR-11, FR-12, FR-13, FR-14, FR-17, FR-29 |
| **Phase 4: HR MCP Integration & Leadership Experiments** | Multi-HR tool MCP skill, career dossier pipeline, cohort & individual metric monitor, and continuous leadership dynamic auditing. | FR-15, FR-30, FR-31 |

## **10\. Open Questions**

> 1. **Local Model RAM Thrashing during Concurrent Execution:** While NFR-12 specifies a 16GB RAM baseline, concurrent execution of local Whisper audio transcription (small.en) and local Ollama LLM parsing under heavy background telemetry loads must be benchmarked to prevent swap thrashing on 16GB unified memory systems. *(To be monitored during Phase 1 bench tests).*

## **11\. Assumptions Index**

> * \[ASSUMPTION: Voice Ingestion SLA\] 10-second Whisper latency is achievable locally on modern Apple Silicon / CUDA hardware using whisper.cpp base/small models.  
> * \[ASSUMPTION: Literature & Web Digest Frequency\] Background polling of RSS feeds and HTTP web pages in article\_sources.md once every 24 hours is sufficient for non-urgent literature citations.  
> * \[ASSUMPTION: Token Budget Cap\] Capping frontier LLM calls strictly to morning focus briefings, pre-meeting dashboard synthesis, complex research tasks, and 1:1 sessions keeps monthly token spend below $20/month under typical PM query volumes.  
> * \[ASSUMPTION: Spoken Protocol & Fuzzy Matching\] Spoken anchor extraction coupled with fuzzy search matching against local Work Items achieves ![][image4] confidence for minor phonetic speech recognition errors (e.g., matching "WI-2260" to "WI-226").  
> * \[ASSUMPTION: Transcript Retention Default\] A 30-day default retention window for raw audio transcript text files provides sufficient runway for retrospective auditing while keeping disk usage lightweight.

# **Addendum**

## **Decisions Log**

> * **2026-08-16 (Strategic Pivot):** Pivoted product thesis from team delivery tracking to Executive Personal PM Coach and Voice Concierge.  
> * **2026-08-16 (Interface Focus):** Deprioritized CLI as an everyday primary UI; established Telegram as the primary voice, briefing, and 1:1 dialogue surface.  
> * **2026-08-16 (Noise Filtering Boundary):** Explicitly rejected forwarding raw Teams/Slack notifications into Telegram. Voice interface scoped strictly for high-context draft synthesis.  
> * **2026-08-16 (Post-Merge Doc Alignment):** Removed mandatory pre-merge doc blocks; code is ground truth.  
> * **2026-08-16 (Anti-Burnout Privacy):** Anti-burnout telemetry (FR-16) restricted strictly to private 1:1 dialogues; excluded from public dashboards.  
> * **2026-08-16 (Model Strategy):** Adopted deterministic code/local model priority for regular tasks, reserving Claude 3.5 Sonnet for high-level synthesis with an explicit migration path to 100% local operation.  
> * **2026-08-16 (Literature Ingestion):** Integrated FR-17 literature recommendations into 1:1 coaching dialogues and daily briefings as contextual citations rather than standalone reading lists.  
> * **2026-08-17 (Deep Inquiry & Doc Sync Addition):** Added UJ-5 and formal requirements to support asynchronous on-demand activity retrieval, decision synthesis, operational guidance, and discussion-to-documentation drift checks over Telegram.  
> * **2026-08-17 (Daily Standup Dashboard Integration):** Added UJ-6, JTBD-7, FR-32, FR-33, and associated Glossary entries to automate daily standup meeting preparation, cross-platform participant activity research (Git, Teams, Outlook, transcripts), and prior daily commitment validation.  
> * **2026-08-17 (Spoken Protocol Anchors & Research Execution):** Added UJ-7, JTBD-8, FR-05, FR-06, FR-07, SM-7, and associated Glossary entries to handle in-meeting spoken protocol anchors, direct verbal commands to 'John'/'pm-ai', Work Item mutations, and automated background research dispatch.  
> * **2026-08-17 (Dual-Authorization Boundary & Missed Meeting Analysis):** Updated UJ-7, added UJ-8, JTBD-9, FR-06, FR-08, NFR-06, SM-8, and updated Non-Goals & Glossary to formalize authorization rules: explicit in-meeting commands execute immediately, implicit discussion extractions are staged via Telegram Interactive Approval Cards (\[Approve\]/\[Edit\]), and missed/optional meetings can be processed on-demand via voice command.  
> * **2026-08-17 (Restoration of CLI Console Interface):** Reinstated terminal CLI (FR-18) as an interactive REPL shell and command interface.  
> * **2026-08-17 (Refinements & Corrections Update \- v0.6.0):**  
  1. *Meeting Preparation Scope & Automated Inquiries:* Expanded JTBD-7, FR-26, and FR-32 to cover all scheduled meetings (15m prep default; 1h prep when automated FR-26 inquiries are required to clarify agenda items).  
  2. *Transcript Recovery & Fuzzy Search:* Updated FR-01 with a two-tier recovery process (\>85% confidence fuzzy match proceeds automatically; otherwise presents manual PM choice in post-meeting report).  
  3. *Post-Processing SLA & Cost Metrics:* Updated FR-03 meeting post-processing SLA to 600 seconds and merged FR-27 cost metric into FR-03 header summary.  
  4. *Direct Command Confirmation:* Updated FR-05 to mandate explicit inclusion of automatically executed direct commands in post-meeting reports.  
  5. *Implicit Metadata Extraction:* Enforced parsing/suggesting target Work Item numbers, assignees, priorities, and flags in FR-06 implicit extractions, and merged FR-31 into FR-06.  
  6. *Interruption Policy Clarification:* Updated FR-13 and Non-Goals to permit scheduled pre-meeting prep alerts (15m/1h prior) and post-meeting summary/approval cards.  
  7. *Meta-Coaching Scale & HR Approval Handoff:* Updated FR-14 to a 1-10 rating scale. Updated UJ-4 and FR-31 so extracted 1:1 goals require explicit PM approval before MCP HR sync.  
  8. *HTTP Source Monitoring:* Expanded FR-17 to monitor both RSS feeds and arbitrary HTTP web pages.  
  9. *CLI REPL Shell Default:* Defined default pm-ai execution in FR-18 as an interactive REPL session ending on exit/quit.  
  10. *Dynamic Persona Adjustments:* Added CLI/Telegram persona modification support in FR-20.  
  11. *Merged In-Band Invocations:* Merged FR-26 into FR-07 for direct pm-ai verbal invocations and fact-checking.  
  12. *Telemetry & Decision Log Consolidation:* Consolidated decisions into event\_log.md and event\_telemetry.db in FR-27.  
  13. *Individual/Cohort Metric Monitoring:* Updated FR-30 to support PM-defined custom metrics, tracking durations (1m, quarter, year, indefinite), trend dynamics, and automatic inclusion in employee dossiers.  
  14. *Secrets Encryption:* Enforced encryption at rest for secrets in config.json under NFR-08.  
> * **2026-08-17 (Commitments Storage & Lifecycle Definition \- v0.6.1):**  
  1. *Directory Contract:* Added .project-ai/memory/commitments\_log.md to Section 2.1 to persist team meeting commitments across git clones.  
  2. *Glossary:* Added Meeting Commitment Ledger & Lifecycle definition covering status states (\[STAGED\_APPROVAL\], \[PENDING\], \[FULFILLED\], \[ALTERED\], \[BROKEN\]).  
  3. *Requirements:* Added **FR-34** (Spoken Commitment Persistence, Lifecycle Management & Ledger) to define extraction, persistent storage schema, automated state transitions via telemetry, and append-only auditing. Updated **FR-33** to explicitly query this ledger.  
> * **2026-08-17 (Mindful Goal Alignment & Anti-Burnout Weekly Planning \- v0.6.2):**  
  1. *User Journeys:* Added **UJ-9** (Andrei conducts mindful weekly and daily focus planning across goal horizons and burnout limits) to model proactive multi-horizon alignment (Operational, Tactical, Strategic) combined with Anti-Burnout Shield telemetry triggers.  
  2. *JTBD:* Added **JTBD-11** (Mindful Multi-Horizon Planning & Burnout Prevention).  
  3. *Requirement Traceability:* Updated cross-references across **FR-02**, **FR-09**, **FR-10**, **FR-11**, **FR-13**, **FR-16**, **FR-18**, **FR-19**, and **FR-27** to explicitly map to UJ-9.  
> * **2026-08-17 (Extensible Telemetry & External System Connectors Architecture \- v0.7.0):**  
  1. *User Journeys & JTBD:* Added **UJ-10** and **JTBD-12** for managing and expanding telemetry connectors (GitLab, Teams, Outlook, HR platforms, Slack, Jira, Notion) via CLI (pm-ai connector) and Telegram (/connectors).  
  2. *Glossary:* Added External System Connector and Connector Schema definitions.  
  3. *Requirements Catalog:* Added **FR-35** (Extensible External System Connector Framework & Dynamic CLI/Telegram Management) defining connection health check probes, encrypted storage in config.json, modular event normalization, and hot plugin loading without daemon restarts.  
  4. *Cross-References & NFRs:* Updated **FR-02**, **FR-18**, **FR-19**, **FR-23**, **FR-28**, **FR-32**, **FR-33**, and **NFR-08** to incorporate multi-connector telemetry sources and secret encryption standards.

[image1]: <data:image/png;base64,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>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABUAAAAYCAYAAAAVibZIAAAAV0lEQVR4XmNgGAWjYFCAvegC1AD/0AWoAWyAuAxdkBrgHBCbowsiAxMy8S0g3seAA/iRia9BMQsDlcBEIPZGF6QEKAJxJ7ogpeATugA1wGF0gVEwCmgIAHMMERIo6OyIAAAAAElFTkSuQmCC>

[image3]: <data:image/png;base64,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>

[image4]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADsAAAAYCAYAAABEHYUrAAACaklEQVR4Xu2Wu2sVURDGP6MY38GgIKK9giIWBgtNQAkqJKighYKCpfg32IghiEVIIyiKhUmVLkVMYalWgqYICeILQVEsfKD4fsyXOcfMmezduzc35AH7g4/dmTnZzNyZc3aBkpK55rBokXc69nnHQmO16K9op+id6GQa/s+gqNc7Z4tm0Wtoos9ETWl4gg+iU6K10Pgx0ftkBfBddMbYfB7VLWoXXQz2H7NmVlkKLTTC8YvdscTErTYlK9S30tmRjeH6WLTC+HNp8Y46Ycc820S/nS926Ipov4tFbHHEP2MPahxfduKF6C6qHwJFYIJnnW9H8Fu8nQXXrHe2xRdfmAbRiOi5aLmL1QL3HZO6Y3zs9l5jE594Fi9F18L9OlG/ifEsKDy+edwWfRJt8IECLEa6D1loR7JCYYz7bVR0X/RLtCRZofDwGUDaRY59j7FnhJvQJLb7QBV4eNiCWZCH/kZjDwVfEWzhPPnfQKdyRuiCJtLqAxkcEH0N922YLLhaMlug6877gIOjbbeZ/YF+mvtpcw760BM+kEFWd5ig93PcLTwzuGbM+S2HRJeNfQu63SLXUcchG1/aRT/FjmNqURH6d4f7J8FeNhnGquDjW6ES3FKWb0i3yBFM41V6AzoSW32gCvxwyCs2wk5/MTY5CF1T6ZPwFdI9Tt4iLfaoaJexcxmGvjrsu61WmPAF56P9yNibRU+NTdiluNc9nZj6THJJ9NnYbFLuGDP4APrP7VjVw0do0ePh2peGJzgNjbFjvN5Lwwn8Rq4E/zYW+MMGsuCM5/4ac8xV6OFViTXQQ+qhD5SUlJSUzDf+ARRrkX6UFLeOAAAAAElFTkSuQmCC>

[image5]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADsAAAAYCAYAAABEHYUrAAACeUlEQVR4Xu2WS6hOURTHl0dc7+6NicfkZkApGXAlj5AQ8p4oylDcmYmBiUS3O5CSIkYYGSgDI2WCKQaiGwaK3O7AI/J+rP9de7v/ve75vnO+c4hb51f/zllr7bP3Xvt1tkhNzb9mq2qUdzrWeMdIY4rqp2qRakC1Jw3/5rrqtHdWJW90Ix2ql2IdfaaaloYHeaPaq2oXi+9SvU5KiHxW7Scb9UEnVetUx4P9g8pUZrVYpag8j3FiiUYwQHF2mNhx1uykhPkmOTsyMzz7VBPJXxqMPBo45ANNwIx5Fqi+O1+cobOqtS4W4eSAr2O5/IHle0SsoR0+UAB8d8D5FgY/4+0sUGaGsxmffEucEVv/S32gBbDv0Kmb5MNsryAb+I5n8Vx1PrxPV12hGM6CUsv3muqTqtMHSjBG0n2IRDcnJQzEsN8equ6qvqnGJiUMDP5VSWcRy/4U2YXpFmt4mQ9UAIcHJ4yEPPCPJ/tG8BWBE8fJ/0r1gHy5HBZrbKcPtMh61cfwvkqGEs7rzDyxckd9wIGlPYFsHqCv9F4I/LhRwUEfKEjW7KCD3o/lzowWK/PI+ZmNql6yL6nekX1Bit8FElaKNX7CB5qwW4YnFYE/HnxPgt02FJbJwXebfB7sawbnDG+RbaolZLfMXLGbDEYtD1wcmiUbwUx/IBtsECvT6Er4QtI9DvolTXa7ajHZpcG17pZ3ZoAOH3M+2PfJnqN6SjbALMW97tkiw+sEPar3ZF+Uksu4Cm/Fkn4cnpfT8CD7xGKYMTzvpOEErKxG4NuY4BcOjETOiR1ejZgqdkjd84EsZqk2FVSVm9V/Aa5hXQU1P3xTU1NT81f5BXMbl7PeAeS1AAAAAElFTkSuQmCC>

[image6]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADsAAAAYCAYAAABEHYUrAAACaklEQVR4Xu2Wy6tPURTHl0ce1yuPa+AxN1AyIANSJHQJxURRBgb+CBMpyUAmN/fGCCMjBkaGGEi5d8CVPHLritS9iLwf3+9v7+1+9/qd38vv+qHOp76ds9ba55y9zlp7n2NWUvK32QVN8k7HJu/435gD/YBWQ6+g/Xn4F1ehM97ZKRZAzy1M9Ak0Lw9XeA0dgOZbiO+FxrIRZp+gQ2LzftQJaAt0PNrfZUxHmWYh0QTbL1VHSRNXLctGBN8sZyeWxONDqEv8E0aPdxTAinlWQt+cL1WoF9rsYglNjvh7rLc/0L790BdohQ8UwAkecb5V0a94uwiO6Xa24pNvi+vQG2ixD9SB646T4rUJVnuD2MRPvIhhCy+aLIIuSYx7QdvtOxW6Dz2DZrpYM0yxfB0y0R3ZiABjXG/3oFvQVwvP9nDzuWx5Fdn2p8VumbnQS+gONNnFWoWbhybMhDz0Txf7WvQ1gybOnf8FNCi+miyF3lv4Vk0EW6EP8XyjjSfcaDLcDzjuqA842NracfqCuK/UhQ9hC531gd+kqDqcoPez3RV2E8cMOb+yHTol9gXordjnrPGfVoVU4Ss+0AL7rDqpBP3r4vmjaM8YD9vs6LshPg+Lony0fInshtaK3RA+dAS6bU2+JYE/DvWSTbDSfLHKNgtjav0Sck66xgn3GE12D7RG7KZhm92FnlpegUZwwsecj/aA2Muhx2ITVimtdc9Oq74nOQm9E/u8tV6gKtjao9BCH6gBv81M+kE8XszDFQ5aiLFiPN7Mwxn8R64Fr00JftZAuxz2jg7QZ/U/hfxkcpNiF5aUlJSU/NP8BGWMkcjX9kHzAAAAAElFTkSuQmCC>

[image7]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABoAAAAYCAYAAADkgu3FAAAA2klEQVR4XmNgGAVDGTACcSi6IBbwAYhjgVgQiPmBOASI36OoIAA4gbgEXRAL+I8Fy6CoIAB4gLgUXRALABncDsTTgNgZTY4owMtAvEUUgUFp0S0gvgrEx4D4DxCzoKhAAuiRSQjLQrSBAYjPjsTfBhUjGhDrI3SgwQCxqBZdAhcg1iJmND4TA8Si62jiOAExFt1hgBjKgSQGyhYgsSNIYngBMRY9AuKvaGIeDBCLotDEcQJiLAIlirtoYj+A+DuaGBygpypCWBqiDQzioGJPofRRJLlRMApGAgAAfvZCcD64fTEAAAAASUVORK5CYII=>

[image8]: <data:image/png;base64,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>

[image9]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAFgAAAAYCAYAAAB+zTpYAAAB80lEQVR4Xu2YPyjFURTHD4ukJGwGo4mEksJiV4RFMij5E1EmZbEyme2S3cBswUCJzcIigyKSEue8e97r/E6/8/q9d5/rDfdT31738zu3c34X770fgEgkEolUAw2YE8wP5gJTk7xclC3MK+YDM6euhWYDs6ilwGfWsve2gTvYel638Lq2UGFzizkV6xvMmViH4ADzBW5mylLycgGfWX32wjvmULlLzKdymkZwN6Qh16RlIKwD9pnVZ28OKpxSbpN9Ma4gvYbcvpaBsA7YZ1afvTAMrnBQ+Vn2zcpL6LrVOM2HwDpgaybLS6wayydYA1fUo/wk+37lJVYDy4eg6g54G1xRl/Jj7KeVl1gNLB8C6rusJdgzWV5i1Vg+wTy4om7lJ9iPKC+xGlg+D31L6cuYDt6TFeq7oiXYM1leYtVYPkH+PXhA+Rn29BXOwmpg+TztmNGMGeI9WaG+q1qCPZPlJVaN5RPUgSsq51vEG6TXkLvTMhDUmz5XND6z+uzNQYV7yh2zl9ChS+iHomsIcr1aBoJ6r2sJpc1a8ftM+22l9bhYv7DTn9DkFsR6h91/0Aqu966+wGSZ9c/ukx43v/mVNuo/s07MvXIEPV5T/TnmGtzTXyn/x6gER5hnzCPmgV+fwD0+S7LMWs33GYlEIpFIJBKpDL+iadKxNH7XdQAAAABJRU5ErkJggg==>

[image10]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADsAAAAYCAYAAABEHYUrAAACYklEQVR4Xu2Wy6tNURzHfyjPPBJ3wFhSStw8yuMWSXK9BhQzmbjKn6AkkQxkSAzkMTEzUoyUiQnqemRAKSIDz7xf36+11vFb37P3Pfuce9Gp/alvZ/++v7XW+f3OXmftbVZT87/ZDI1SU1itRrcxGfoJLYReQjvzdIPL0Ak1/xXToWcWCn0ETc3TDfZDb6EP0G7Jkc/QLhdzPeoItBY6FOMfbsyQcBIn7NVEh4y10GiC2y/dHc896KqLB6EbLiacN0nixKz4+RCa6PxKLLew2DFNtMlrNcB86LuLp1heeILeNIk9fg2ywoa5fedY2D4XNVERFjgg3oLoJ25LnKB3WuKZEnu0+Y6ZAb2CrmuiBZzDoq45j3d7pYuZ18KJ+k+gU/Ga9VxwOZ4FbW/fVoyHHlv4j42RXBEck4qm2Gh/NqK5qUSRz8PnkuV3cQ103MUjSo+Foq9oogQeHr7hu3m6sClS5iu+cZ78z6E7zuuIudAX6KwmhmAd9DFe99mfBnwxZU2V+R5u7Qku9uO/uuvKrLKwyGFNVKCoWBbo/bKmyvzEesufFucsPKcTPNxavWk12GHhyzp95m6z8mLpL4vX72Ks0LuvpuObxJ8s/4tsgZa4uJB9Fr5okybahC8ORU0Q72+XOEGvV83IU2iceC8sb3YrtNjFTRyAFqk5DFjwQfEY89nq4bg9Lub2LPoByEZrXpMchd67+Iy1sY1HijcWCn8QP8/n6d/wkGHupoXDi1uyrFC+5JTBNdI8HqZdzUlotJoOvnrykLqliSJmQxsqKh0wXQtfw5ZW1Lw4p6ampuav8gtssZ7Pe69xvQAAAABJRU5ErkJggg==>

[image11]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAYCAYAAACbU/80AAABFUlEQVR4XmNgGAWjgHZAH4jfAvF/ID4BxAKo0qjAlQGiMAtdgkyQAcSTkPhLGCDmGyGJYQXWDBCF3egSJAKQGSBMSAwnUAXin0C8DF2CSPCEAdMykhwAAyJA/B6ID6FLkAiqGCCWe6FLEAs4gPg+EF8DYmY0OUIggAFi+UR0CVKBGBB/AOId6BJ4QA8Qrwbiv0DsjCZHNFAH4l9AvBBdggQgzQAJhS3oEviAHQNEUxu6BJmA6EQYyUB5mQAK8tloYjAH2KCJw0EuA0SBH7oEiSCYAbtvYWJYE3EDAxGlFAkAZBE7El8PKrYNSYymQAiI/zFALH0DpaeiqBhsAJRNvInEFlA9VAWgItecSKwJ1TMKRgFVAACQPj7zS9oyhAAAAABJRU5ErkJggg==>

[image12]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACoAAAAYCAYAAACMcW/9AAABS0lEQVR4Xu2VsUoDQRRFnxaijaQw4C9YWAmWKfwIwdomURQDtnZamU8wWKbIByjYxiZYKEhSiYI2wUJQEDGBeB+zK5NrXnazGqs5cAh7b2b3sSwzIoFAYB+WOPQ4gK/wHW5SN3Fq8BP2I7cG629a8MK7voWX3vUPVjn4Q6xB58V1jGY5DmNm4ANswKnB6tdYg16LPegJh8w0vIH3cI66rFiDxp8FY+UmZ+I+8kUuxmTig8acwh5c5iIl+tBtDsUeyMpTcyTuBgUuEtA1OxyKPZCVp0bfit5gg4sEdM0uh2IPZOWJHIpbuMZFSnTtHofgTYYPpFmbw1FUYRcucTEm+uAyh2Bd7EFXOBzGOXyBeS4ysCDuwRUuIrQretfHUWaim/wVvIOz1GWhDp/hE3yMfjvijlUf3at1sKa4/ftDEg4cPUJH/iEQCAT+ly/0xVeF9Tbc5AAAAABJRU5ErkJggg==>

[image13]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADsAAAAYCAYAAABEHYUrAAACVUlEQVR4Xu2Wy6tOURjGX8o9kpySiZlSDCRKyYAMJGGiGJGJkj9BIpIMpEyIDGRwMlOilBETySW3kluKyEAuud+ep3et4/2eb6/vO/s7p+Oo/aunb7/Pu/b+3rX2Wmsvs4aGf806aIyawgo1/jemQr+hhdAbaHNreoBz0BE1RxIW+ti82BuSy+yC3kOfoG2SI1+hrSHms6gD0CpoX4p/hTYjzhbzIsaleDf0biDr3IcuhfgudDXEhM+YInFmdvp9CE0OfkeWqDFE+syLmhi8/EYy0yTO0JsuceSnxMus5vQdDz2Drlj3TWAwaMeIjvwta29D6J2QmIMX44h2ftCMhW5DT6FJkqsDC7qTrpear12lakCI+s+h4+l6JnQm5J5Y+yD2xAXzjWOWJrowz7xYFsWB43o7mryIdipT5XPzOWutb3EldDjEw8Ip6Ae0QBMFNlm54M8hrmpDSr4SOz4DemU+uMPCfvMilmtCWGPe7qX4l5OfKXWq5Ec4teMyi+2/h+ue2WH+UL65Tswxb3dafH706S9OcalTJT+zGjoUYv4Pl1uGm1vPm2z+aNc5irF9v3jnkz83xR9SrNB7oGaASyryBboX4vXWw6f0pPmU4IZTFxb8SDz91GyUOENvkZqJF9AE8V5ba2c32N/Z05WL0Ftr/bbVZb61d4Txngpve4g5PfW+zFpor5rgIPQxxHxJHacxk9fNz7Hx1DMUdpoXns/GPMsq3GSYu2a+k3JKlgrlGbkEn5Hv+xYTVXCOl/5kNHDM/MBTgkdPblI3NdHQ0NDQMNr4A4U6modT7H1kAAAAAElFTkSuQmCC>

[image14]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACUAAAAYCAYAAAB9ejRwAAABaklEQVR4Xu2VTStGURSFNwMfYcLQRzFSSjIgJSnJmDCRzIwNmDDiHzA1MJQ/IPkDEv9CmciQRD72es8+9x6r695zU+/oPLV6z1rnvPvu7sc5IolE82hRrXFYwoHq07REc6BLda36Vt2Jq1+bTtUuh3/woTq1cbvqS9WRT0u/uGZQE/SZb81WRNKt2uOwgGfVU+CPxV1wKsheVBeBB/eqN8oq6ZHqpobFNTBE+Th5rFmnbN/yWsQ0dSN54TbVYjDnmRO3ZpbyLct7KS8lpikUhc5V86ox83iEnh3LJoMM4CNCPk15hi8eq0H3t8wfmgcjlg2YPzLPj3TZ8g3KS6lzpxhkjzbeNj+RTzdYtXyB8lL+25TP/Ts1k0832LQc20U0MU3dSnVT2LcwbtrXNyrFhZGdkT8JPLi0vBYxTYFX1VXgcdzwxYruCvwKZb/wtztW/B48WP4u7ogpOj6wbeBcxC/WYqtIJBKJKn4AALJu5oS78BoAAAAASUVORK5CYII=>

[image15]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADsAAAAYCAYAAABEHYUrAAACTElEQVR4Xu2Xu2sUURTGT7SIDxQNxsJ0gohlFI2gpFAsgsRHI2gXbETwTxBExRAsRISAYoog2thZBewEQWx84AMsFATBoKBE8f36Pu7c5cy3c93ZWR8szA8+st85c+/ekzlz565ZTc3/ZjfUo0Fhmwa6jSXQT2gQegUdyKcbXIPOavBfsRV6bWGht6B5+XSDo9Ac9AE6KDnyGRpznvNR49AO6GTmf7hrfgsHccBhTVTkHDTpPAvh/KtdjDyCrjv/ALrpPOG4xeIjq7K/T6BFLl6KLRYmO62JNuEcQwUxv9Cl4iOMLRPv+S6eHdRR+66x0D5XNFEC3gUtjGjsrvgIYxfF94v3aPGVWQG9gW5oogUnLHSJR4tVH9H4c+hC9pnruexyT61C+7ZiAfTMwjM2X3JlYQF+E9GiIkVxjrtq+bu4HTrj/B9lJfQWmtFECe5bKMDfhaKiSCqu+ML7oJfQPRerxFroCzStiZJwo+Li/XNHUkWl4h629kLn/fVf3efSDFuY5JQm2mC5hTl6NWHpolLxyIjl3xaXLLynI9zcWp20Guy38GWdvnN5iNBFc2GRd9acJ4w91qDjm/hP0EPn90CbnC/kiIUv2qWJihSdaHxsn6WL3aDBjBfW3CWzli92L7TR+SaOQes12AF8xmM7qjz0h5xne+o1kVHouAbBBPTe+Slro407ZcCaC4z66K4j3GQYv21hJ2VLphbKQ04KzhHH8R/d1Zy39A8JwqMnN6k7miiCd2NnSW3OxnQtPIbxfVhG67IxNTU1NX+VXw2gnf6Y3pgtAAAAAElFTkSuQmCC>

[image16]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAF8AAAAYCAYAAACcESEhAAADAElEQVR4Xu2YS6hOURTHF8ojhRDFUJLyppDHHUgGpAgloSh5dEUxUUoGJoyMTUQkEyMJE8oEhfIaUYhk4P3Ma/3tva99/nev853vfLej7zq/+nfu/q+9z1nfOvfsvc8Rqamp6R2MY6OmGiartrH5PzJc9Vz1S/VINTQbzmWw6qK4sTdUfbJhkxdsROyR/BuzX/VO9Um1mWJ5lM21i8XiBm/nQEn6iyt8AAnh/NMjz2KsuL6DfHuEb/ft6mGD4sWcVn0TNz7v991XXYrad1XXorZFK7l2Y564wYc50CRv2FAmqX6wmeCj6gx5N1VfyGM2qRaxGWEVf4i4GANvGJtE2VxzGa/6qjrFgYIgcX7Ep3q/Eeizhrx93s/jJxuEVfzbkj43vGNsEmVzLcRI1WvVVQ40AGOQwOXIw9OwIGqnWChu3HzyN3of64jFHTYIq/jwU8Wy/EAruTbFQNVjcXNjP4qlQJ+QPITCL8v0SLNLXP8Z5K/2/mzyA0dVY9gkerr4ZXMtxShxRbzAAQMUI74B97LhJAfF9Z1C/grvryM/kFekAPrsYFPsIlt+oGyuTTFB3I7hOAdyWKL67P/ukL8/pNHUsEVcv2nkr/J+akHFFvYEmwkwvpNNsYts+YEyuRYmzGmHOFCAVNJPJO3HhGvOJX+997G1Y65IsakQ43eyKXaRLT9QJteGrBU3ODU/FiHMeSngz2EzYoC4Ps3sICyfQT/M08x7SZ8D3gM2I8rkaoJHEoOWc6BJ8CJlXZx93KjR5KEPFtCY895nZqn2smmA8bvZFFe81LnhzYzareZqckC6r9qtgItjQYpBG3vqQHjr5URT/zloryQPvGTDAFtmnOMIBzyIbY3aeMmMc+iJXCvlrbhEHvrjyWz4D+fEfXNh8FkAb8M4YmxqugAf2CDOql6pnqme+iNuGDYQMfg8gOtcF7cpwBsqf6NpNddexQZx36TaHqzMSwsqb8Gsku9stCuYE/FGVkQT/Zh/zS02aqoB395551FTER1s1NTUtAu/AU096pnRg8EWAAAAAElFTkSuQmCC>

[image17]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHkAAAAYCAYAAADeUlK2AAAER0lEQVR4Xu2Ya8iNWRTHl5kxrlEuQy69J3e5pDGFEkpSlCJMyCVFfFAITaYxpYYZShKSD4QiUm4xjUaTD74YkiZjvkyTy5ghxngzLrmuv73We5ZlP+c853W8c5yeX63OXv+9n7332ft59l57E2VkZGSkYZoXGpjuXsgoL+PYXrB97jMaiAFsC734ntCIrZsXK4kPKEzuBvn9Rn7b20LCSQp5d9nmujylBeXL/UxhANLwlxfeE+5Q+K+wiuUp22BJ2476TsNvLGks6/Bv5bNf0Vn0ZuK3FR8vUjFqvVCBXPaC0JveHK8GY7wXIsQm9rbTv2c7bHxwnEKZCUb7j22/8cE5tkdO82BVGO3FCqM1JU9yD/ofJnk72xO2Pj4jAjqHL1DT4BCFL1xBGnmTjdZPtJtGgz/V+GCl6IV47oUKBC9r0iTnqPh/LBs/st1j+8RnFACdgy2Q3xh4CXY7bRSF8ufFHyH+cC0gzBa9jdMtF50/ie0Lth3iN2FbwvY15bcMsIJCGXxJMYay/UIhRhjr8pLa2MT2sRYSvqTwH65RCFBhlq6UH7umbF+xbaQ366k3H7H9ynaF8nthKXSk/ETDHrL1fK1EnB8olO8v/mLxP60rEZgi+hCnKxjUTk5bxvYnhecwGYtEXytaDdtPomG1gma3DfA32zHjn6JQp1KsDYwrwAu0VDSsWkjDLBqL4L8m1VMvWlFoFMtImsCmEB9S+JrsZKP+JFAeZRA9K6tFG2g0MFH0GU5XkBdD9zm/x0Pzyzu0f4yPZ2L1Qltj/EJtYJXwWtJyrZOcpp5UoEIEOEd9RhlAp+yXnQS+dl2mlfkUnhnkdOzj0GOBFYKZPV4UchSeq3E6tK0RzfYX6QvGVzS2UHLix9r4NqIVm+Q09aQCyxM6u81n1BMs83MkrQOAJdgOhuUS2wEvUn5PHub0maJrcGc5TWFViNGFwnM+voC2LqLZ/iJ9xvjKv/R6uUJtfBfRfnOaopOcpp6S0C/6iM8oEXQEAYWmre45SOGyxKLPInDBM6VE10k60IHzAVts4KD5vuPl9fhyhdqIvUi/G3+VSddQ+nrqRUsKAcRZSn+7ZMFRS9EBwBWdnwDsLf7asQPbFuPjGQRSlhOiez5jW+5FQ47Cc+2cHhs4P3l4+WNtQsPpQ8mJFmtjfUS7anzcECq6t6ep563Asod96A8KYXxaEOrvlTQ6hSMKfnvVlQj7qQ6kN3s0iX218BG9euz5OgZu4fBsX6dD2xXRbLvNxbfB3nTRLIXa2Om0WtEBjpu5fFZJ9ZQNLOGINnGtmAZcU2pQgi87trckmY/s97E9k1/k42gV474XDLghu0FhK8DvY7bNFI5F0K5T+CJxPsXVqmq4qVPQLxybtJ9YUSxp21CwSj6gUBeOj0qp9ZSdeV4ogr6p75pZbGO8mFFd2CvTjColdobNqCJwr4uoPKOKGemFjIyMjOriJY44VcaRaHELAAAAAElFTkSuQmCC>

[image18]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADsAAAAYCAYAAABEHYUrAAACaElEQVR4Xu2WPWhUQRSFbwxEjZiIPxG0sBKxCKKiBgwpIhYi8acRtBGxEcE6lSAiBkkRLQ1JIcE0dlYhgoVgI0r8TcBCUQkogoqKaPy9hzuT3D3OS96uq7LwPjjsnnPfzM68nTfzRAoK/jd7VXUcEp0c1BqLVT9VG1WvVYdKy9NcVV3g8F/RoLohNtBRqnlOqt6rPqmOUg18UR1xHv1BPaqdqjPB/3DXzAoaocFxLlTIJrH+moPfEjwzrrrm/EPVTecB2i0iH1kVPh+rGl2ei+1infVyoUzQx71E9sD5ppAxyJaQ93wn3y5/uHzXii2fYS7kYIXYAPspvxPyyF3yEWQD5NGn9x6efMUsV70Ve/byclhsQOcpvx7yCL7zwAHnz2XmxmE8l13tiVSwfOdigeqp2DNWTzVmjaT/2WchXxY8TyqSyrH5XJHSf3GHqs/5qtKieqca4UICDPZ+IoOwWXnPZOWMn/hS1Uv5fZ8om3WqKdUlLswC7joGjOMHdIttTsjmhSxrUlm5B0t7ofP++q/ue246xDo5y4WcrBQ7Xx+pWsWeLz+orEll5ZFdUnpaDImd0xFsbnO9aU1zUOzHqnXmRngSH8hHkE1w6PhG/rPYDY3sU211PskJsR/aw4UK4InFbL/zB0LGINvMYWBSNZ+yV1I6WfxG3BeSnBJ766kWGDBe/yK3VW+cj+C6Y85jeaZuAOhSneZQOaf66PyglLGMq8EGsUG/CJ9Z5zQ2GdRvie2kWJJZA8VLThboI7bDZlrTXJSZXTwFXj2xSY1xIcVq1e6cagttaha8hm3LqfWhTUFBQcFf5RdhQ6F3fjmnSAAAAABJRU5ErkJggg==>

[image19]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADsAAAAYCAYAAABEHYUrAAACeUlEQVR4Xu2WS6iNURTH/x55P7qe5ZGRMNCN8igyIEXyKhMKmSpjo1uSSAaYKGJwuzExM5AMDIQRCaHk0b1SpJBwL67H+rf2vtZed59zv3McV6e+X/07Z/3X/va33/sDSkr+N5tFQ7zpWO2NZmO86JdokeitaEea7uOS6KQ3B4sRouvQhl51ucgH0U5Ri2iiaJvofVIC+CraY2LWRx0RrRUdCvFPU6YqfIgP7PWJOlkMrY8dIEtC7IkNt5qVlFBvrIsjM8LvE9EY4xdiBbSyYz5RI6zjXsZ7kPE4Q6dEa1wu4gfph4tX4i+X71zo8rngEwWYCm3gGeffCb7FxzlYhnXa2OI7XzdToHuIe68ou6ENOuH8a8G3+DhHF/4MHNtz3uSeo47lOxCjRC9Ej0TDXM4zB/mZ7Qz+ZOMx5n57KLol6hUNN/kID5+LSGeRy/64iRvKNOjpecUnMrAT9zMexcPKeiNNfDl4RbAdnyR6jf7nRM3ME30TtftEFTjqbDSvH7IfejjRGxoLZZgPLdPmEw4u7dEmtgP03fwvzCpoJYd9oiDTofcrl+hC6P7ys+a3BAeCZR4737Ie6W3RIfpo4rMY+Eurj+3QFzbqzo3EZRx5GmKeB5FxwbthPA/3taUHOqCRLaKlJs6yD/qiTT5RB75j0dtqYi7FzyYm66DlKn0SvkK6x8kbpJ3lO+y50I8D0K+eRsEGfzHxbdE7E5PZomfO4yx1Oy+yUXTQm8JR0ScTn0MNy7gRtEI7/DL8Vrqnd0HznDH+3kzTCfzIqQSfjR3kYdrUnEb1U3wC9JC66xM5Zoo2FNTy8EzTws+wZQW1IDxTUlJS8k/5DZHsmiLefehIAAAAAElFTkSuQmCC>

[image20]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADsAAAAYCAYAAABEHYUrAAACd0lEQVR4Xu2WuWtUURTGjwvuCxG1MFqKEQRRSBTcIEFU3CEWCgqWCv4JgoQQEQuxERQtxMTGLkWwsBJjm1i4IC4gKIqFC4r78n0598Zzz7yZeTOJysD7wcd7Z7l37pm7vCtSUPC/2Q1N8E5Hu3c0GrOhX9Aq6DV0IA2P0g+d9c5/xTzohehAn0Bz0/AIb6GDUJNovBN6k2SIfIEOG5v9USehzVB3sH+anIqwERsc9YE6mSJaaITLL86OJQ7canGSob6Zzo4sCs+H0Azjz8U60c5O+0CNcMY8K6Afzhdn6BzU4WIRWxzxfayXMS7fpaLL56oP5IQDPOJ8K4Pf4u0smLPA2RZffN3MF91DN32gCmzDQd0wPs72BmMTP/AsnkEXwjvH02diPAtqXr7VmAY9he5Bk1wsC+bYfchCdyQZCmPcb3eh29B3aHKSofDwuSbpLHLZnzH2uLJQdNDXfaAMPDxswSzIQ/9UYw8EXx5s4Tz5X0J3jK8ulkFfocs+UIEt0Kfwvkn+FFxtMC2iecd9wMGlPd3Y9g/6Zt5zs1G0kx4fyEHW7HCA3u+3xETRnPvOb9km6dfiCvTe2Bel+k1rlP2iP1jvN3eflBYVoX9teH8UbJ4HkVnBd8v4PNzXls+SbpE9UJuxMzkm+kO7fKBGeHGoVGyEM/3R2GSraE65K+FzSfc4eSVpsXuhVmOXcAJa7Z1jgAPucj7aw8ZeAj02NuEsxb3u2SmlfZJT0AdjX5IalvF48U606Afh2ZuGRzgkGuOM8TmYhhN4ySkH28YCeZg2NOdFD69yzBE9pIZ8IItmaHtOxQOmYeE1bE1OLQ9tCgoKCv4qvwGdWpd6t1TgrQAAAABJRU5ErkJggg==>

[image21]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAYCAYAAACbU/80AAABG0lEQVR4XmNgGAWjgHTAiC6AA+gD8Vsg/g/EJ4BYAFWadODIADGsBV0CC8gA4klI/CUMEL1GSGJEg1gGiOYcdAk8AKQehAmJ4QWVDBANQegSRIAnDJiWEe2AKUD8D4gt0CUoAFUMEMu90CWQwXog/gHESugSFIIABojlE9ElkEEuA0SRFboEhaAHiFcD8V8gdkaTwwpKGCAOCUaXoBBIM0DM3YIugQtEMUA0ZKNLUACIToTIwI4BoqkNXYIAAAX5bDQxmANs0MSJAipA/BOI56BLYAGg6MPmW5gYM5o4SUAQiPejC2IBIIvYkfh6ULFtSGI0BUIMkLIEZOkbKD0VRcVgA6Bs4k0kpmYJCQciQGxOJNaE6hkFo4AqAAARED8soZRH4wAAAABJRU5ErkJggg==>