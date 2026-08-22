# Scope Model — pm-ai

Companion to `SPEC.md`. Four scopes, what each holds, and the rule deciding which one a record belongs to. Storage tiers, encryption, and retention are in `storage-contract.md`.

## The four scopes

**Application Scope (`~/.pm-ai/`)** — System-level state owned by the application: daemon settings, the registry of enrolled projects, per-project connector configuration, encrypted credentials, operational telemetry, diagnostic logs, and the disclosure ledger. Deliberately separate so no employer- or project-specific configuration lands in the sovereign personal scope.

**Sovereign Personal PM Scope (`~/.manager-ai/`)** — The independent personal coaching hub: leadership philosophy, 3-tier goals, Socratic 1:1 coaching logs, literature subscriptions, anti-burnout metrics. Contains **no** project-specific information or configuration. Survives independently across project, role, and company transitions. Governed by the User Privacy & Data Boundary Charter.

**Team-Member Scope (`~/.pm-ai/private/people/`)** — Gitignored, 600-permissioned records *about direct reports*: career dossiers, goals agreed in a team 1:1, per-employee monitored metrics. Stored under the application scope but governed by its own rules, because two requirements turn on telling it apart from the sovereign personal scope: **these records may sync to an external HR platform on explicit PM approval, and personal-scope records never may.** Deliberately **not** part of the sovereign scope and does not survive a company transition — a single directory, deleted on leaving the role.

**Isolated Project Scopes (`<project-root>/.project-ai/`)** — Repository-specific, committed to version control: project rules, task automation scripts, team cultural conventions, local daily dashboards, meeting records, and the commitments ledger. Its one gitignored subdirectory, `transcripts/`, holds raw meeting captures.

## The ownership rule

**Scope is decided by subject, not by convenience.** A meeting record and its transcript live in the scope that owns the meeting: a team meeting in its project, a 1:1 with a direct report in the team-member scope, a purely personal session in the sovereign scope. A committed record may cite only a meeting in its own scope, so the capture is never more or less shareable than the event it records.

Every scope that owns meetings holds its captures at the same relative path (`transcripts/`), the way each holds its own `event_log/`. That is three of the four:

| Capture location | Holds |
| --- | --- |
| `<repo>/.project-ai/transcripts/` | Team meetings belonging to that project |
| `~/.pm-ai/private/people/<person_id>/transcripts/` | 1:1 sessions with that direct report |
| `~/.manager-ai/transcripts/` | Purely personal sessions |

The application scope holds none, because it owns no meetings. **All three are equally protected** — by exclusion from version control, wherever version control can reach them. They are not encrypted at rest: a capture's exposure is publication to a repository, not the disk.

## A. Application Scope

```
~/.pm-ai/                              # SYSTEM-LEVEL STATE (no personal records)
│
├── config.toml                        # Daemon settings & global defaults
├── disclosure.md                      # Frontier-call provenance & cost ledger - never committed
├── projects.toml                      # Registry of enrolled projects (pm-ai project add)
├── connectors/                        # Per-project & personal connector CONFIGURATION
│                                      # and IMPLEMENTATION - the hot-loadable plugin
│                                      # modules, plus each instance's settings
│                                      # (type, domain, cadence, enabled). At 600,
│                                      # gitignored, NOT encrypted.
│                                      # Carries no token and no secret of any kind:
│                                      # every credential goes to private/config.json,
│                                      # which is encrypted. Incl. team-member career
│                                      # MCP (HR platforms) - operates on the
│                                      # team-member scope, never the personal one
├── logs/                              # Rotating structured diagnostic logs (NOT event_log/)
│
└── private/                           # OPERATIONAL ENCLAVE (gitignored)
    ├── operational.db                 # Tier 2: job queue, cursors, executed-key ledger,
    │                                  # staged proposals (600, never rebuilt)
    ├── derived.db                     # Tier 3: search & commitment indexes - disposable,
    │                                  # rebuilt by pm-ai reindex
    ├── config.json                    # API credentials (encrypted)
    ├── vector_index/                  # Pruned embeddings - NOT encrypted, rebuildable
    └── people/                        # TEAM-MEMBER SCOPE - never committed;
        └── <person_id>/               # a single deletable directory per report,
            │                          # removed on leaving the role.
            ├── memory/
            │   ├── meetings/          # 1:1 SUMMARIES - citation root for this
            │   │                      # report's record. Never readable by that
            │   │                      # report's peers, so never project-scoped.
            │   └── event_log/         # Team-member-scope audit trail
            │
            ├── transcripts/           # RAW CAPTURES of 1:1 sessions with this
            │                          # report (excluded from version
            │                          # control, 30-day purge)
            │
            └── ...                    # Career dossiers, agreed 1:1 goals and the
                                       # PM-configured metric files are named at
                                       # runtime, so none is declared here.
```

## B. Sovereign Personal PM Scope

```
~/.manager-ai/                         # INDEPENDENT PERSONAL PM COACHING HUB
│
├── rules/
│   ├── manager_principles.md          # Personal leadership philosophy & career guidelines
│   ├── persona.md                     # Personal coach persona, tone & constructiveness
│   ├── communication_preferences.md   # Executive briefing preferences & voice triggers
│   └── article_sources.md             # PM-configurable literature & web HTTP sources
│
├── memory/
│   ├── daily_dashboard.md             # Manager Strategic Focus morning briefing
│   ├── strategic_goals.md             # 3-tier goals (Project, Team, Personal career)
│   ├── coaching_1on1_history.md       # Socratic 1:1 logs, meta-feedback & growth notes
│   └── event_log/                     # Personal-scope audit trail & decision log
│
├── skills/                            # PERSONAL CONCIERGE & CAREER SKILLS
│   ├── telemetry/                     # Global cross-project telemetry harvesters
│   ├── synthesize_manager_dashboard.py
│   └── anti_burnout_shield.py         # Workload telemetry & PTO guardrail analyzer
│
├── private/                           # PERSONAL ENCLAVE (gitignored, encrypted -
│                                      # the one enclave still encrypted in full)
│   ├── telegram_cache/                # The PM's own voice notes & dialogue state.
│   │                                  # Transient input; never a backup target.
│   └── personal_analytics.db          # Burnout metrics, workload & calendar-density dynamics.
│                                      # Separate DB by design: project-scope rendering never
│                                      # opens it, so personal analytics cannot be joined into
│                                      # team-facing output. Tier 2: backed up, never rebuilt -
│                                      # burnout trends outlive the telemetry they came from
│                                      # once compaction runs.
│
└── transcripts/                       # RAW CAPTURES of purely personal sessions
                                       # (excluded from version control,
                                       # 30-day purge). At the scope ROOT, outside
                                       # private/ - so if the PM keeps this scope as
                                       # a private git repository, the private/ rule
                                       # does not cover captures and the capture
                                       # guard is the only thing that does.
```

## C. Isolated Project Scopes

```
<project-repository-alpha>/
│
├── .project-ai/                       # PROJECT-SPECIFIC CONTEXT (committed to git)
│   ├── rules/
│   │   ├── persona.md                 # Project assistant persona definition
│   │   ├── conventions.md             # Project team cultural rules
│   │   └── engineering_specs.md       # Architecture & code guidelines
│   ├── memory/
│   │   ├── daily_dashboard.md         # Project daily team dashboard
│   │   ├── commitments_log.md         # Spoken commitments & promise tracking ledger
│   │   ├── meetings/                  # Meeting SUMMARIES - citation root for every
│   │   │                              # extracted fact (man-hour cost, attendees, duration).
│   │   │                              # Committed: a commitment in this scope may only
│   │   │                              # cite a meeting in this scope.
│   │   └── event_log/                 # Project-specific audit trail & decision log
│   ├── skills/                        # PROJECT-SPECIFIC SKILLS
│   │   ├── parse_standup.py
│   │   └── sync_gitlab_wi.py
│   │
│   └── transcripts/                   # RAW CAPTURES (gitignored, not encrypted)
│                                      # Verbatim transcripts & audio; 30-day purge.
│                                      # The one gitignored directory inside a committed
│                                      # scope, so its exclusion rests on a rule rather
│                                      # than a directory boundary - and a rule can go
│                                      # missing. The daemon asks GIT before writing,
│                                      # never the rule text, because a text check gets
│                                      # two of three real configurations wrong.
│                                      # A 1:1 with a direct report is people-scoped instead;
│                                      # the capture always lives where its meeting lives.
│
└── .gitignore                         # Contains /.project-ai/transcripts/
```

## Boundary rules that follow from this model

- Personal-scope files are never indexed into or committed to project repositories; pre-commit hooks verify the private enclaves are gitignored.
- A capture write asks **git itself** whether the directory would be carried into a commit, and the question is keyed on whether the path lies inside a git working tree — never on which scope owns it. All three capture locations are covered on the same terms, so a private personal repository is protected exactly as the employer's is. Tracked, or unanswerable, refuses the write; outside a working tree there is nothing to be excluded from and the write proceeds.
- Anti-burnout indicators and personal workload analytics are excluded from every project-scope file.
- **Encryption is narrow as of 2026-08-22**: credentials (`config.json`) and the sovereign personal enclave (`~/.manager-ai/private/`). The operational store, raw captures and team-member records are 600-permissioned and gitignored but not encrypted, with full-disk encryption as the backstop. A report's record being unreadable by that report's peers now rests on file permissions and the directory boundary rather than on a cipher.
- Custom metrics and dossiers about a report live in the team-member scope only — never the sovereign scope, never a committed project scope, because a report's performance record must not be readable by that report's peers.
- Only team-member-scope material is ever an HR sync payload, and no payload may draw on personal-scope material, directly or by way of a model that read both.
- `strategic_goals.md` holds all three goal domains in the personal scope today. A project-scope alignment surface would first require project goals to exist as project-scope records — a project artifact citing a personal goal is the cross-scope violation this model exists to prevent.
