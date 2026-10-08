---
title: 'A command picks its project by AD-11''s order, and the personal dashboard works from anywhere'
type: 'bugfix'
created: '2026-10-08'
status: 'done'
review_loop_iteration: 0
baseline_commit: '3ba560882c7ff5bc80c7154b9a353351f72d87d5'
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** With two projects enrolled, pm-ai refuses your own dashboard whenever you are outside both project folders.

`pm-ai dashboard` renders your personal dashboard, and `pm-ai goal set` writes your personal goals. Neither belongs to a project. But pm-ai decides which project a command is "in" before it even reads the command, and when the folder you are standing in names no project, it refuses every command that needs its working parts, including these two. Naming the project does not help either: `pm-ai dashboard --scope project:beta` is refused the same way, because the name is read after the refusal has already happened. From inside alpha's folder, the same command renders beta's dashboard while pm-ai still believes it is acting in alpha, so anything it records along the way lands in alpha.

The architecture (AD-11) already settled how this should work. It is not built.

**Approach:** Read the command first, then decide where it acts. Every command states its target: a named project, your personal space, or pm-ai's own application settings. A command whose target is not a project acts there, from any folder, and is never refused for want of a project. A command that targets a project acts in the first of: the project named on its command line, the project whose folder you are in, the only enrolled project. The project it acts in is the project its target names, so the two can never disagree.

## Boundaries & Constraints

**Always:**
- **The command line is read once**, before pm-ai decides anything about projects.
- **A command never acts in one place while its output goes to another.** What pm-ai records along the way goes where the output goes.
- **A named project that is not enrolled is refused**, naming the enrolled projects. It never falls back to the folder.
- **A named project wins over the folder** and lifts every folder refusal: outside every folder, one folder under two names, an unreadable working directory.
- **`pm-ai dashboard` with no `--scope` stays your personal dashboard**, wherever you run it.
- **Every enrolled project is still watched**, whatever a command targets (story 4l).
- **One project enrolled behaves as it does today**, apart from the corrections below.
- `doctor` says truthfully how the project was chosen: named, folder, only project, or not needed.
- The refusal sentences stop saying a project cannot be named on the command line.

**Ask First:** Any change to which commands accept `--scope`. Any change to an exit code.

**Never:**
- No new command-line option and no `--scope` on any command that does not take one today.
- No change to how often anything is checked, and no scheduler.
- No configured default project for the CLI (AD-11 rejected it).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Personal dashboard, two projects, outside both | `pm-ai dashboard` | personal dashboard written; acts in personal | N/A |
| `goal set`, two projects, outside both | `pm-ai goal set …` | goal written | N/A |
| Named project, outside both | `pm-ai dashboard --scope project:beta` | beta's dashboard; acts in beta | N/A |
| Named project, inside alpha's folder | same | beta's dashboard; acts in beta, not alpha | N/A |
| Named project not enrolled | `--scope project:gamma` | refused, naming alpha and beta | existing refusal code, 3 |
| Personal dashboard inside alpha's folder | `pm-ai dashboard` | personal dashboard; acts in personal | N/A |
| Application command, two projects, outside both | `pm-ai config show`, `pm-ai connector check` | runs | N/A |
| `doctor`, one project, run elsewhere | `pm-ai doctor` | selection line says "the only enrolled project", not "the working directory" | N/A |
| Unparseable `--scope` | `--scope alpha` | usage error, as today, before any project decision | exit 2 |

</frozen-after-approval>

## Code Map

- `pm_ai/app/entry.py:104-191` -- `main`: reads `arguments` (118), calls `_compose(keychain)` (121) *before* `dispatch(arguments, …)` (123); the order to reverse
- `pm_ai/app/entry.py:450-512` -- `_acting_project(projects)`: one project → acting; cwd unreadable / outside all / innermost / two ids at one depth; no named-project input
- `pm_ai/app/entry.py:541-634` -- refusal builders; stale sentences at 553 ("no way to name a project…") and 611 ("Picking between them by name…")
- `pm_ai/app/entry.py:663-767` -- `_compose`: undecided → `_Composition(None, …, undecided=refusal)` (705), so **no daemon is built**; else `build(None, project_id, watched=sorted(projects), …)` (714-720)
- `pm_ai/app/entry.py:804-833` -- `_selection_probe`: always "chosen by the working directory" (823)
- `pm_ai/app/wiring.py:192-203`, `:277-278`, `:366` -- `build(root, project: str, *, watched, …)`; `scope = DataScope(PROJECT, project)`; `_watched` adds the acting project to the watched set; `Daemon(scope=scope)`. `Daemon.__post_init__` (154-177) refuses a project scope that is not watched and accepts a non-project scope
- `pm_ai/app/wiring.py:713-755` -- `_enrolment_scope(acting=…)`: an enrolment row with no project of its own files into the acting scope when one project is watched (745-746). With a non-project acting scope it must use the only enrolled project when there is one, and otherwise skip with a warning as today
- `pm_ai/app/pipelines.py:164`, `:193` -- empty harvest persists into `daemon.scope`; citation guard checks `into=daemon.scope`. `run_dashboard` (253ff) uses the scope passed to it, not `daemon.scope`
- `pm_ai/surfaces/cli/dispatch.py:1216-1276` -- `TABLE`; only `dashboard` declares an option (`SCOPE_ARGUMENT`, 715); `_options` 1449-1500; leaves take positionals only
- `pm_ai/surfaces/cli/dispatch.py:1332-1446` -- `dispatch()` builds `Context` (1356) then parses; this parsing becomes a step `main` can call first, returning the command and its target scope
- `pm_ai/surfaces/cli/dispatch.py:392-433` -- `require_selection`, `require_daemon` (which calls `require_selection`)
- `pm_ai/surfaces/cli/dispatch.py:731-797` -- `_scope` (`None` → personal), `_dashboard`
- Targets today: `dashboard` → `--scope` or personal; `goal set` → personal; `config show`, `key enrol`, `connector add`, `connector check` → application; `doctor`, `setup`, `project add` → need no daemon
- `.importlinter` -- `surfaces` may not import `app`; `main` may call into `dispatch`, never the reverse. Precedent for injected callables: `diagnose`, `_dashboard(daemon)`
- `tests/slice/test_every_enrolled_project.py:135-150` -- `machine` fixture (HOME, keychain, cwd outside every project); `compose()` helper calls `entry._compose(Keychain())` with no arguments; rows 295-399, 505, 530 (`connector check` refused — becomes a pass), 549, 560, 580
- `tests/surfaces/test_cli_dispatch.py:650-666` -- `rendered` fixture monkeypatches `entry._dashboard` as `lambda daemon: record`; 669-741 dashboard and option rows

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/surfaces/cli/dispatch.py` -- split parsing from running: a pure step returns the command and its target scope (or a usage error); `dispatch` runs an already-parsed command; `require_selection` is consulted only by a command whose target is a project -- the command line is read once
- [x] `pm_ai/app/entry.py` -- `main` parses first, then composes for that target; `_acting_project` takes a named project that wins and is refused if not enrolled; refusal sentences and the `doctor` selection line say how the choice was made -- AD-11's order
- [x] `pm_ai/app/wiring.py` -- `build` takes the acting scope rather than a project name, still watching every enrolled project; `_enrolment_scope` places an unowned row by the only enrolled project, never by a non-project acting scope -- personal and application commands get a daemon
- [x] `tests/` -- one test per matrix row, plus: the acting scope equals the output scope for every dashboard row; update the rows and fixtures named in the Code Map -- the matrix is the contract
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the AD-11 build-slice entry resolved

**Acceptance Criteria:**
- Given two enrolled projects and a working directory outside both, when `pm-ai dashboard` runs, then it exits 0 and writes the personal dashboard.
- Given the change, when `uv run pytest` runs, then everything passes with 24 skipped.

## Design Notes

After this change no current command targets a project without naming it, so the folder rule and its three refusals are reached only through `doctor`'s report and by the first future command whose default target is a project. They stay, tested, because AD-11 binds them.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass, 24 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Suggested Review Order

**Read the command first**

- The order reversed: parse, then compose for the command's target.
  [`entry.py:121`](../../../../pm_ai/app/entry.py#L121)

- The one parse step; every command states its target here.
  [`dispatch.py:1449`](../../../../pm_ai/surfaces/cli/dispatch.py#L1449)

**Choosing where a command acts**

- Non-project targets skip selection; a project target goes through AD-11's order.
  [`entry.py:532`](../../../../pm_ai/app/entry.py#L532)

- Named wins, unenrolled is refused, then folder, then the only project.
  [`entry.py:555`](../../../../pm_ai/app/entry.py#L555)

- How the choice was made travels with it, so doctor can say so truthfully.
  [`entry.py:465`](../../../../pm_ai/app/entry.py#L465)

- Composition now builds a daemon in the target scope, watching every project.
  [`entry.py:806`](../../../../pm_ai/app/entry.py#L806)

**Wiring**

- build takes the acting scope rather than a project name.
  [`wiring.py:192`](../../../../pm_ai/app/wiring.py#L192)

- An unowned enrolment row is placed by the only project, never by a personal target.
  [`wiring.py:726`](../../../../pm_ai/app/wiring.py#L726)

**Tests**

- The bug itself: the personal dashboard outside both folders.
  [`test_every_enrolled_project.py:660`](../../../../tests/slice/test_every_enrolled_project.py#L660)

- Named project wins over the folder, and acts where it writes.
  [`test_every_enrolled_project.py:703`](../../../../tests/slice/test_every_enrolled_project.py#L703)

- Every daemon-backed command must declare its target.
  [`test_cli_dispatch.py:891`](../../../../tests/surfaces/test_cli_dispatch.py#L891)
