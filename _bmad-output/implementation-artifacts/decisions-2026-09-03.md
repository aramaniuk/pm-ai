# Decisions — first-run setup and project onboarding, 2026-09-03

Taken with the human while triaging the 2026-09-03 wave-1 review. Each entry is
the decision, then what it changes. Several change the architecture spine, which
is skill-derived: they are recorded here and land in `ARCHITECTURE-SPINE.md` only
through a re-run of the architecture skill.

## D-7 — `pm_handle` is mandatory at first boot

Not declinable. `4h` prompts until it has one, or refuses.

**Changes:** `4h`'s "operator declines a value" matrix row is withdrawn — there
is no such path. `4g`'s probe reports an unset handle as unhealthy without
ambiguity, and the review's B3 (the `WARNING`-forever problem) and the declining
half of B24 both disappear. `4h`'s exit-`0`-on-second-run criterion survives,
because a configured machine always has a handle. Both specs are approved, so
these amendments need the human's unlock.

## Q1 — git is optional per project

pm-ai must not require a project to be under git.

**Changes:** already true in code. `service.py:714-717` treats `working_tree`
returning `None` as an *answer* — no repository, nothing can carry the write into
a commit, so it proceeds — and only an unanswerable question refuses. `4d`'s
`Always` ("a registered path must be an existing directory containing a git
repository") is stricter than both the code and the intent, and is relaxed.
The residual requirement is that git be *installed* where a project is a
repository, which is `1g`'s probe.

## Q2 — `pm-ai project add <path> [alias]`, and rename after onboarding

The argument is a filesystem path, not an id. An optional alias names the
project. A project may be renamed after onboarding.

**Changes:** the id becomes independent of the path, which removes the
basename-collision problem entirely (`~/work/alpha` and `~/oss/alpha` differ by
alias). **Rename is not a `4d` amendment.** The id is the scope directory name,
so changing it moves `projects/<id>/.project-ai/` and staleness every `SourceRef`
citing that scope — the same reason `4d`'s `Never` already excludes removal. If
rename only changes a display alias and leaves the directory name fixed it is
cheap; if it changes the id it is a migration. **Open: which.**

## Q3 — normalise the path, or use the alias

A directory name that `_directory_name` refuses (uppercase, leading dot,
whitespace, separators — `paths.py:264`) does not block onboarding: normalise, or
require an alias.

## Q4 — paths are resolved to absolute at registration

A relative path is meaningless to a daemon started from another working
directory. `projects.toml` stores absolute paths.

## Q5 — onboarding an existing directory keeps its Tier-1 artefacts

**As originally stated the instruction was a no-op:** a project tree declares
nine `Tier.TRUTH` artifacts and *zero* Operational or Derived ones. Tier 2
(`operational.db`) and Tier 3 (the indexes) are application- and personal-scope,
per machine, never inside a project. So there is no project Tier 2 to drop and no
project Tier 3 to regenerate.

The live question it exposed is Q6.

## Q6 — the four machine-local project artifacts

`event_log/`, `commitments_log.md`, `daily_dashboard.md` and `meetings/` become
`gitignored=True` in the project tree. `rules/` and `skills/` stay shared.

**Why.** They are committed today, so two machines on one repository write the
same files and a `git pull` rewrites Tier 1 underneath the local Tier-2 and
Tier-3 state that was derived from it. Concretely: both machines append to
`2026-09.md`, so every pull conflicts or interleaves; `_append_batch` publishes
the whole file through `os.replace` (`1m`), so a local append clobbers lines
pulled in between; the `seen` dedup set is per-machine, so the next harvest
re-appends what a teammate already logged; sealed segments are declared immutable
(`2g`) and a pull rewrites them; and `2f`'s "file order is arrival order, and it
is the only exact one" is false after a merge. `daily_dashboard.md` is replaced
whole daily by each machine, and `commitments_log.md` is append-only with the same
clobber window.

`rules/` and `skills/` are human-authored, hand-edited, and nothing local is
derived from them — which is what sharing is actually for.

**Changes:**
- `scope_model.py`: four declarations flip, project tree only.
- The `GITIGNORED` set grows, and that set is exactly what
  `_assert_git_excludes` guards (`service.py:697-717`). So in a project that *is*
  a repository, every write to those four refuses until `.gitignore` carries the
  rule. **`.gitignore` generation becomes mandatory**, which answers `4d`'s
  `Ask First` by force rather than by choice.
- `memory/` itself is declared `gitignored=False` while all four of its children
  become `True`, so nothing committed remains under it. Whether the directory
  declaration follows its children is a loose end.
- AD-3, AD-38 and `scope-model.md` describe the project scope as the committed
  one; they need re-deriving.

**What this gives up, stated plainly:** a teammate's harvested events, meetings
and commitments never reach this machine's ledger. CAP-10's retrospective counts
and every project-level aggregation become per-machine. Cross-user visibility of
project *events* is dropped deliberately; cross-user sharing of project *rules
and skills* is kept.

## Q7 — `seen` and the derivability question

Moot under Q6. `seen` stays Tier 2, because no pull can now introduce Tier-1
lines it has not seen. Worth recording that `seen` *is* derivable from Tier 1
while AD-3 declares Tier 2 "not derivable from Tier 1" — a latent
inconsistency, no longer load-bearing.

## D-2 / D-3 — `ConnectorPort` gains `sample_events()` and a health method; `Probe`/`Health` move to domain

**Changes:** `sample_events` is called by the AD-34 gate as `connector.sample_events()`
and exists nowhere; `8d`'s Code Map wrongly places it in `registry.py`. Both members
go on the port and on `GitLabConnectorAdapter`; the registry only enumerates.
Because `pm_ai.ports` may import only `pm_ai.domain` (`.importlinter:208-226`),
declaring a health method **forces** `Probe` and `Health` out of
`pm_ai/platform/doctor.py` into `pm_ai/domain/`. `Report` follows them.
`run_all`'s signature does not change.

## D-3b — `doctor` reports registry membership only, and probing is a separate command

Health probing is **connector-specific**, implemented by each connector, not by
`doctor.py`. `doctor` keeps its five machine probes and lists registered
connectors without contacting anything; `pm-ai connector check` does the live
probing and owns CAP-35's 10-second bound and any concurrency. No last-known
health is persisted — stale health on a diagnostic screen is worse than none,
and persisting it would mean a new Tier-2 table and the first `SCHEMA_VERSION`
bump.

**Changes:** `8d` gains a CLI subcommand and therefore a dependency on `4c`,
having had none — it was an independent starter and gates `8b` and `33a`, so the
build order lengthens. `4c`'s exit-code table gains the subcommand deliberately,
since `4c`'s `Never` forbids other slices extending it. A per-probe deadline
cannot cancel a blocking adapter call: the bound is on waiting, and a hung probe
is reported `FAILING` while its thread is abandoned. That must be stated or an
implementer writes a bound the adapter can ignore.

## D-4 — `HarvestResult` widens to carry records

`33c` emits both `Meeting` records and `CALENDAR_EVENT_HELD` events.
Deriving records from events is **impossible**: `MeetingHeldPayload` carries
`meeting_id`, `attendee_count` and `duration_minutes` — a count, not the
attendee list — and no `title`, `start` or `calendar_event_ref`. So the result
widens, connectors produce domain records (as they already produce
`NormalizedEvent`), and `app/pipelines.py` writes records through `11a`'s
accessor before persisting events, satisfying `33c`'s ordering rule.

## D-6 — `Sanitized` becomes unforgeable by `__post_init__`

The invariant is that `for_model` is a **fixed point of the sanitizer** —
`_INJECTION.sub(for_model) == for_model` — not that it equals
`sanitize(raw).for_model`, which would reject `pipelines.py:70`'s legitimate
`ex.for_model[:80]` truncation. Forging a *clean* `for_model` stays possible and
is harmless: clean text is all a model was ever going to receive.

**Changes:** answers `8e`'s open `Ask First` by force. `__post_init__` needs
`_INJECTION`, `Sanitized` moves to `pm_ai.domain`, and
`AD-30 — pm_ai.domain imports nothing from pm_ai` (`.importlinter:163-166`)
means **`sanitize()` and the pattern move to domain with the type**.
`pm_ai.core.sanitize` becomes a re-export or its callers re-point.
Rejected: a factory-only constructor (Python cannot privatise a dataclass
`__init__`, and `dataclasses.replace` bypasses a sentinel) and a `NewType`
(erased at runtime, so it satisfies mypy and guarantees nothing).

## D-8 — "3-Tier" is `GoalDomain`, and `23a` is wrong

Settled by the source, not by preference. `prd.md:63` —
`strategic_goals.md # 3-Tier Goals (Project, Team, Personal Career Goals)`.
`prd.md:424` — "**Domain** — `Project` | `Team` | `Personal` (career growth).
This is what a goal is *about*, and it is the `<Tier>` in the alignment tag,
matching §2.1's '3-Tier Goals'."

So `alignment_tag`'s docstring (`goals.py:99-104`) is correct and `23a`'s
`Always` is the error — misled, presumably, by "Milestones" in the section
title. `23a`'s clause is corrected and its section groups by domain.

**Note:** all three domains live in the personal `strategic_goals.md`
(`scope_model.py:541`), so a project render cannot show even Project-domain
goals. The boundary statement is needed for a reason the scope model states
outright: "there is no project-scope counterpart".

## D-9 — a non-TTY refusal exits `3`

`4c` defines `2` as usage and `3` as a stated, deliberate refusal. Refusing to
prompt into a pipe is a refusal. `4h` is the spec that changes — two matrix rows
and one criterion — and `8b` was already right. Needs UNLOCK.

## D-10 — meeting scope, record format, and amendments

**Scope (POC).** A harvested calendar row's `Meeting.scope` comes from
**connector configuration mapping an Outlook category to a project**. The PM tags
the meeting in Outlook; the mapping lives in `connectors/` — application scope,
Tier 1, gitignored (`scope_model.py:451`) — so it is per-machine and uncommitted,
and stays out of `config.toml`, whose vocabulary `4a` closed at three keys. An
**unmapped meeting defaults to the personal scope**: it is the PM's own, it still
appears in the personal dashboard, and nothing is silently dropped.

**Record format.** Reuse the ledger's field grammar — `key=value` per line,
rendered by `render_value` and parsed by `scan_fields` — with a free body. No new
dependency, one grammar the repo already tests, and it extends to `22a`'s goals
so there is one hand-edited grammar rather than three. It is the ledger's
*fields* without its envelope (`- [id] category actor=`), which `11a` must say
outright or the next reader assumes `parse_line` applies.

Known wart: `attendees` is a list in a grammar with no lists, and `parse_line`
refuses duplicate keys, so the list is one comma-separated value. Comma becomes
reserved inside an attendee value, enforced nowhere — a display name like
"Smith, Bob" would split silently. Mitigation: store handles only.

**Man-Hour Cost is computed, never stored.** CAP-1 puts it in the *summary card*
header — a rendered surface, not the Tier-1 record — and it derives from
`blended_hourly_rate` in `config.toml`, so a stored value goes stale the moment
the rate changes.

**`tentative` is stored; `stale` is derived.** Tentative is provider data (Graph's
response status). Stale means "absent from a window we actually harvested", which
`8a`'s `CoverageWindow` already makes derivable.

**The record holds a transcript-derived summary, and amendments are its source.**
Per CAP-1 the transcript is fetched, sanitized and parsed, and "every ingested
transcript binds to a meeting record". Extraction outputs already have homes:
orders to `Proposal`, commitments to `commitments_log.md` (declared, no producer
yet), decisions to `DECISION` in `ObservedEventType`, and the summary to the
record.

The PM amends through the CLI or Telegram — text or voice — not by hand-editing,
so pm-ai owns every write and there is no concurrent editor to merge against.
Amendments are **records with provenance** (when, actor, surface), append-only
and never regenerated; `## Summary` is **derived from transcript + amendments**,
so a corrected summary reads correctly and the amendment log is the audit trail.
`## Notes` is preserved verbatim.

**Wave placement, unchanged:** Graph transcripts are `33e`, the real transcript
path is `11b`, both wave 2 — and a summary needs a model, which decision 2
removed from waves 1 and 2, so summarisation is gated on story 7. Voice
amendments need Whisper; text amendments do not.

**Two consequences:** an amendment mutates a Tier-1 record, so CAP-10 requires an
event-log entry — `SelfActionType` needs a member for it, which is `2c`'s closed
vocabulary and the same class of decision as `23b`'s `Ask First` about whether
rendering logs. And the record now has machine-owned and human-owned regions,
which settles the `11a`/`33c` overwrite contradiction the review found.

## Still open

- **D-5**: how `core.rendering` names a project tree it may not resolve — posed, not answered.
- **Q9 / Q10**: whether a project render keeps the wall as a mechanism, and what its
  3-Tier section says. Recommended `keep the render, drop the wall-as-mechanism`; unconfirmed.
- **Q2's rename**: alias-only, or an id migration?
- **`memory/`**: does the directory declaration follow its four children?
- **`4g`/`4h` UNLOCK**: needed to apply D-7 and D-9 and five review findings.
- **`SelfActionType` member for an amendment**, and `23b`'s analogous rendering question.
- **`22a`'s grammar**: follows D-10's format choice, needs confirming.
