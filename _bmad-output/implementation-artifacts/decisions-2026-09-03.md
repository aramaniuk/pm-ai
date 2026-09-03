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

## Still open

- **Q2's rename**: alias-only, or an id migration?
- **`memory/`**: does the directory declaration follow its four children?
- **D-2 … D-6, D-8 … D-10**: the remaining triage decisions.
