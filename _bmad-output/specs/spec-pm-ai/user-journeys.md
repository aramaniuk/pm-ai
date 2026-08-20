# Jobs To Be Done & User Journeys — pm-ai

Companion to `SPEC.md`. The JTBDs say what the PM is hiring pm-ai for; the journeys are the demonstrable end-to-end scenarios each capability must add up to. Capability ↔ journey mapping is in `traceability.md`.

The user throughout is **Andrei**, an engineering PM.

## Jobs To Be Done

- **JTBD-1 — Context-enriched delegation & communication.** Draft detailed, high-context replies across tools from brief voice or text instructions, without manually searching tickets, specs, or spreadsheets.
- **JTBD-2 — Socratic growth.** Surface unexamined leadership blind spots, time misallocations, and strategic drift through telemetry-backed dialogue.
- **JTBD-3 — Contextual learning.** Receive situational literature citations and leadership frameworks at the moment an operational challenge arises.
- **JTBD-4 — Frictionless closed-loop meeting accountability.** Materialize spoken commitments into Work Items and continuously verify progress against real execution telemetry, without noisy reminders.
- **JTBD-5 — Career cultivation.** Review synthesized pre-meeting engineering dossiers combining sprint output, custom metrics, and long-term HR growth goals before 1:1s.
- **JTBD-6 — Deep asynchronous information & telemetry query.** Ask for multi-source activity breakdowns, historical decisions, DevOps procedures, and documentation consistency checks without logging into multiple tools.
- **JTBD-7 — Telemetry-enriched meeting preparation.** Arrive at scheduled meetings with auto-researched participant activity and agenda analysis, prepared 15 minutes prior — or an hour prior when owner inquiries are needed.
- **JTBD-8 — Zero-friction in-meeting task automation & research.** Issue verbal commands by name during meetings to mutate Work Items, record priorities, and dispatch background research without breaking conversation flow.
- **JTBD-9 — Dual-layer meeting authorization & missed-meeting analysis.** Distinguish explicit commands from implicit discussion extractions, and ingest transcripts for missed or optional meetings on demand.
- **JTBD-10 — Terminal-native interactive CLI access.** Run commands, open natural-language prompts, and trigger background skills from a loopback-bound REPL at full parity with the Telegram text interface.
- **JTBD-11 — Mindful multi-horizon planning & burnout prevention.** Calibrate weekly and daily schedules against 3-tier goals while capping calendar density, under a private data boundary charter.
- **JTBD-12 — Extensible telemetry & connector lifecycle.** Configure, enable, disable, and expand external connectors via CLI or Telegram with encrypted credentials, input sanitization, and hot-swappable schema normalization.

## User Journeys

### UJ-1 — Weekly Socratic 1:1 session

**Entry:** Friday afternoon, authenticated via cryptographic pairing on Telegram or a loopback terminal session. *"Let's start our weekly 1:1 session."*

1. pm-ai opens with a telemetry breakdown of actual time allocation vs. quarterly strategic goals (e.g. 80% debugging ticket specs vs. 20% delegation).
2. It evaluates the Anti-Burnout Shield and flags elevated workload (e.g. 3 consecutive 10-hour days) held in the personal enclave.
3. It asks a targeted question: *"What specific blocker in Project Alpha prevented you from handing off the auth refactor to Alex this week?"*
4. Andrei reflects by text or voice note.
5. pm-ai cites a delegation framework from `article_sources.md` with a direct citation and proposes an actionable experiment for next sprint.

**Climax:** Andrei commits to the experiment; pm-ai logs the decision to `event_log/` and `coaching_1on1_history.md`, then prompts the Meta-Coaching Scorecard.
**Resolution:** Andrei rates the session in 5 seconds; persona tuning updates without interrupting live work.

### UJ-2 — High-context technical replies from a 20-second voice note

**Entry:** Away from the desk, cryptographically paired Telegram voice channel.

1. One 20-second note: *"Draft a reply to Laura explaining the webhook contract changes in v2.1 based on yesterday's architecture meeting, and tell Alex to proceed with Schema B for Auth caching."*
2. pm-ai transcribes and sanitizes, then cross-references recent transcripts, architecture specs, and active Work Items.
3. It generates individual draft cards one by one, each showing recipient, full enriched body, and cited sources.
4. Andrei taps `[Send]` on the first, `[Edit]` then `[Send]` on the second.

**Climax:** Both replies land in their real channels via authorized MCP skills, with no spreadsheet or spec opened.
**Resolution:** A complex communication queue clears in 60 seconds of mobile interaction.

### UJ-3 — Wrapping a meeting with zero administrative fallout

**Entry:** A 45-minute architecture sync ends in the calendar.

1. pm-ai downloads, sanitizes, and processes the transcript within 600 seconds, calculating Man-Hour Cost into the summary header.
2. It parses explicit commands issued during the meeting, executes the qualifying ones via MCP, and reports them in an explicit confirmation section.
3. It extracts Alex's spoken commitment (*"I'll finish Redis benchmarks by Thursday"*), stages a card proposing a timestamped comment on the target Work Item, and writes the commitment with closed-loop verification parameters.

**Climax:** Work Items and the local ledger reflect real discussion state without a ticket editor being opened or an alarm set.
**Resolution:** Over the next 3 days pm-ai watches commits and PR reviews, verifying progress or surfacing a Socratic alert if a dependency risks slipping.

### UJ-4 — Preparing for and completing a team-member 1:1

**Entry:** Calendar event trigger, 15 minutes before a 1:1 with Alex.

1. pm-ai queries local GitLab telemetry (MR velocity, review participation), fetches custom monitored metric dynamics, and connects to HR tools via MCP.
2. It synthesizes a compact Career Dossier and pushes it to Telegram or CLI.
3. Andrei runs the 1:1 grounded in objective data and aligned growth goals.

**Climax:** Post-meeting, pm-ai processes the recorded 1:1 transcript and extracts agreed career goals and performance objectives.
**Resolution:** The extracted goals are presented for approval; on explicit approval they sync to the HR platform via MCP.

### UJ-5 — Asynchronous deep knowledge and telemetry inquiry

**Entry:** Telegram or terminal. *"What activity did Alex do yesterday? Show me focus across commits, WI updates, CI/CD, MRs, and calendar events"* — or *"Find information about the auth protocol discussed recently and verify if it's in sync with documentation."*

1. pm-ai acknowledges immediately with a status token.
2. The daemon runs cross-source harvesters across the operational store, transcripts, and project rules.
3. For documentation-sync queries it diffs verbal decisions against committed specifications.
4. It returns a structured card categorizing findings, cited artifacts, timestamped events, and drift flags.

**Climax:** A comprehensive multi-source answer arrives without logging into five tools.
**Resolution:** Andrei responds to a blocker, or issues one command to update out-of-sync documentation.

### UJ-6 — Telemetry-enriched daily standup or team meeting

**Entry:** Scheduled calendar trigger, 15 minutes prior — or 1 hour when inquiries are required.

1. pm-ai evaluates agenda items; anything needing owner clarification triggers automated inquiries at least an hour ahead.
2. At 15 minutes prior it aggregates active work items, blockers, candidate backlog, commitment ledger status, and participant activity across all connected sources.
3. It renders the project dashboard and pushes an interactive summary card to Telegram and CLI.
4. Andrei reviews pre-filled participant summaries, blocker root causes, and unfulfilled commitment flags.

**Climax:** The team skips generic status updates and works validated blockers and agenda resolutions.
**Resolution:** Today's spoken commitments and target dates become the baseline for tomorrow's validation.

### UJ-7 — Explicit verbal commands vs. implicit discussion extraction

**Entry:** An architecture meeting, transcript ingestion active post-meeting.

1. Andrei says: *"pm-ai, update WI-226 with changing requirement A to X and dispatch research on SQLite WAL performance."*
2. Later, the team discusses changing a cache eviction TTL from 60s to 300s for another Work Item, without addressing pm-ai.
3. Post-meeting, pm-ai parses and sanitizes: the **explicit** directive updates the Work Item and dispatches the research job; the **implicit** discussion becomes extracted target, owner, and priority, drafted as proposed updates with candidate ledger entries.
4. A summary card carries the explicit confirmation section and an interactive approval card for the implicit update.

**Climax:** Andrei approves the implicit update with one tap or one CLI command, and pm-ai commits the change.
**Resolution:** Explicit directives execute immediately; implicit discoveries stay staged until approved.

### UJ-8 — Post-meeting analysis for a missed optional meeting

**Entry:** Double-booked, missed an optional technical sync that was recorded.

1. *"Fetch the transcript for today's Payment Gateway Sync and run post-meeting analysis."*
2. pm-ai locates the recording via calendar integration, downloads and sanitizes it, and runs the full extraction pipeline within 600 seconds.
3. It outputs a summary card: explicit requests executed, implicit updates with parsed Work Item numbers, owners and priorities, key architectural decisions, and approval prompts.

**Climax:** Full decision clarity and approvals in 30 seconds, without listening to a 45-minute recording.
**Resolution:** The missed meeting's outputs are indexed in the event log and ledger, staged for tomorrow's standup validation.

### UJ-9 — Mindful weekly and daily focus planning

**Entry:** Monday 07:30, Telegram or REPL.

1. pm-ai synthesizes the dashboard from upcoming calendar load, active Work Items, pending commitments, and 3-tier targets.
2. It evaluates the Anti-Burnout Shield — 26 hours of scheduled meetings and 3 late-evening review sessions the previous week — and warns: *"⚠️ High Burnout Density Alert: Calendar commitment is at 65% capacity. Operational firefighting risk is critical."*
3. It presents a breakdown across three horizons: **strategic** (4 hours for architecture refactor specs), **tactical** (ensure the Redis benchmark validation lands), **operational** (triage 5 open reviews and clear the backlog).
4. It proposes declining two optional syncs and scheduling two 2-hour focus blocks.
5. Andrei accepts, adjusting the block start times.

**Climax:** The dashboard updates, focus blocks are written to the calendar via MCP, and alignment metrics update — before the week's noise begins.
**Resolution:** The week starts with explicit boundaries, goal alignment across all three horizons, and burnout guardrails in place.

### UJ-10 — Configuring and expanding telemetry connectors

**Entry:** `pm-ai connector` in the terminal, or `/connectors` on Telegram.

1. Andrei runs `pm-ai connector add --type jira` (or the Telegram equivalent).
2. pm-ai prompts step by step for domain URL, API token or OAuth key, and sync parameters.
3. It runs an immediate endpoint health probe verifying connectivity, permissions, and polling reachability.
4. On success it encrypts credentials at 600 permissions and registers the harvester into the running radar with no daemon restart.
5. It triggers a 7-day historical backfill and confirms active status, health, and entity mappings (e.g. Jira Issues → Work Items).

**Climax:** The new source flows into morning dashboards, dossiers, and deep inquiries alongside the existing ones.
**Resolution:** The multi-tool ecosystem expands in under 2 minutes with zero plaintext secret exposure.
