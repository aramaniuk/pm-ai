---
title: 'pm-ai works with every project you enrol'
type: 'bugfix'
created: '2026-09-24'
status: 'done'
review_loop_iteration: 0
baseline_commit: '7e71f0d'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Enrol a second project and pm-ai stops working.

`pm-ai project add` accepts as many projects as you like, and the registry, the path resolver and the storage layer all handle a set of them. But the step that assembles the running system takes exactly one project name, so when it finds two enrolled it gives up — and reports it as a **fault**, as though the machine were broken. Nothing is broken: both projects resolve, and enrolling several is the arrangement the architecture describes, where each one is checked on its own schedule.

Two things follow. The second project's work is **never looked at** — no commits, no meetings, nothing. And `pm-ai setup` and `pm-ai doctor` disagree about the same machine, because setup does not consult the step that gave up: setup says healthy and exits 0, doctor says failing and exits 4.

**Approach:** Separate two questions that were being answered with one value. *Which projects does pm-ai watch?* — all of them you enrolled. *Which project is this command about?* — the one whose folder you are standing in. When the folder does not answer that, pm-ai says so and names the projects, rather than picking one for you.

## Boundaries & Constraints

**Always:**
- **Every enrolled project is watched.** Whatever pm-ai checks for one project it checks for all of them, each keeping its own place in the queue so one project's progress never skips another's.
- **A command acts in one project**, and which one comes from the folder you ran it in: the project whose directory contains it. Being in a sub-folder counts. This is by folder containment alone — **not by asking git** — because a project is not required to be a git repository.
- **Ambiguity is refused, never guessed.** Outside every enrolled project's folder, with more than one enrolled, pm-ai refuses, names them, and says how to choose. Writing to the wrong project is worse than not writing.
- **One project still behaves exactly as it does today**, from any folder. Someone with a single project sees no change at all, including when they are nowhere near it.
- **Nothing that separates projects loosens.** Each project's material stays in its own place, and the existing rule against one project's records referencing another's is untouched.

**Ask First:** Nothing.

**Never:** No way to name a project on the command line — that is the next slice, and until it exists the refusal tells you to run the command from inside the project. No new exit codes; a refusal here is the code that already means "a stated, deliberate no". No background scheduler, and no change to how often anything is checked. No searching the disk for projects: a project is watched because it was enrolled, never because it was found.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Two projects enrolled | pm-ai starts up | it assembles and runs; both projects are watched | no longer a failure of any kind |
| A command inside a project | run from that project's folder or below it | acts in that project | N/A |
| A command outside them all | several enrolled, run from elsewhere | refused, naming every enrolled project and saying to run it from inside one | the existing "deliberate no" exit code |
| Exactly one project | run from anywhere at all | acts in it, as today | N/A |
| A folder inside two projects | one project's folder sits inside another's | the innermost one — the nearest enclosing project is the one you meant | N/A |
| An enrolled folder that has moved | registry names a directory no longer there | reported as needing attention, naming that project; the others still work | unchanged from today |
| `setup` and `doctor`, two projects | both run on the same machine | they agree — neither treats a second project as a fault | N/A |
| Nothing enrolled yet | empty registry | unchanged: say nothing is enrolled and how to enrol | unchanged from today |
| Checking a second project | both projects watched | each keeps its own place in the queue, so one project's progress never advances another's | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/app/entry.py:481-482` -- `if len(projects) > 1: return _Composition(None, _ambiguous(projects), ...)` — the refusal to assemble; the central change
- `pm_ai/app/entry.py:410-435` -- `_ambiguous`, whose message says pm-ai "has no way to choose between them yet" and whose docstring calls the working directory an option "not this module's policy to invent" — AD-11 already chose it
- `pm_ai/app/entry.py:484-488` -- `ScopePaths.production(projects=...)` is **already handed every project**; only the `build(None, project_id, ...)` beside it narrows to one
- `pm_ai/app/wiring.py:142-152` -- `build(root, project: str, ...)`, the single-project signature
- `pm_ai/app/wiring.py:206-211` -- `scope = DataScope(ScopeKind.PROJECT, project)` and the eager `resolver.scope_root(scope)` check
- `pm_ai/app/wiring.py:230-262` -- what that one scope currently decides: the skill registry, the built-in GitLab instance keyed `gitlab:{project}`, and the enrolled connectors read for that scope
- `pm_ai/app/wiring.py:264-279` -- `Daemon(..., scope=scope, ...)`; the field that has to become "the project this command is about"
- `pm_ai/app/pipelines.py:155-165` -- `_persist_by_scope` already groups what it writes by each item's own project; evidence the write path is ready
- `pm_ai/app/pipelines.py:188` -- `assert_citation_legal(cited=meeting.scope, into=daemon.scope)`, the separation guard that reads the daemon's scope
- `pm_ai/platform/paths.py:546` -- `repository(project_id)`, which gives each project's folder — what a working directory is matched against
- `pm_ai/platform/vcs.py:80` -- `working_tree`; **deliberately not used**, since a project need not be a git repository
- `pm_ai/domain/health.py` -- the probe shape `_ambiguous` returns today
- `ARCHITECTURE-SPINE.md` AD-10, AD-11, AD-19 -- the rules this restores; two Open Risks are closed by it

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/app/wiring.py` -- assemble against every enrolled project, and take the project a command is about as its own argument -- one value was answering two questions
- [x] `pm_ai/app/entry.py` -- stop refusing to assemble when several are enrolled; work out which project the working directory is inside; retire `_ambiguous` in favour of a refusal that names them and says what to do
- [x] `pm_ai/app/entry.py` or a neighbour -- the folder-to-project match, by containment, innermost first, with no call into git
- [x] `tests/` -- the matrix, including two projects both watched, the nested-folder case, and the refusal's wording
- [x] `ARCHITECTURE-SPINE.md` -- strike the two Open Risks this closes and note that AD-11's folder rule is now implemented

**Acceptance Criteria:**
- Given two enrolled projects, when pm-ai starts, then it assembles successfully and a connector instance exists for each — asserted per project, since "it started" would pass with one of them silently missing.
- Given two enrolled projects, when both `setup` and `doctor` run on that machine, then neither reports a fault and their exit codes agree. This is the disagreement that revealed the bug, and it has to be asserted from both sides rather than assumed to follow.
- Given a command run inside one project's folder, then it acts in that project; given the same command run from a sub-folder several levels down, then it still acts in that project.
- Given a command run outside every enrolled project with more than one enrolled, then it refuses, and the message names every enrolled project — a refusal that does not say what the choices are just moves the guesswork to the operator.
- Given exactly one enrolled project and a working directory nowhere near it, then the command acts in that project, so nobody with one project has to change what they do.
- Given the full suite, then the skip count is unmoved and no test outside those covering composition and project selection changes behaviour.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: no failures, skip count unmoved from 2612
- `uv run lint-imports` -- expected: 12 contracts kept
- `uv run mypy` -- expected: clean

**Manual check:**
- Enrol two throwaway projects under a temporary home, run `pm-ai doctor` and `pm-ai setup`, and confirm both report the same thing and neither calls a second project a fault.

## Suggested Review Order

**The change in one place**

- Which projects are watched, and which one the command is about, stop being one value.
  [`wiring.py:142`](../../../../pm_ai/app/wiring.py#L142)
- The refusal to assemble is gone; the working directory decides instead.
  [`entry.py:417`](../../../../pm_ai/app/entry.py#L417)

**Where it could have leaked**

- An enrolled connector files into its own project's tree, not into wherever you were standing — the one cross-project leak this slice could have introduced.
  [`wiring.py:627`](../../../../pm_ai/app/wiring.py#L627)
- Three different undecided states, three different reasons; the operator is never told to do what they already did.
  [`entry.py:490`](../../../../pm_ai/app/entry.py#L490)

**What an operator sees**

- `doctor` names the project a command binds to, and never calls the absence of one a fault.
  [`test_every_enrolled_project.py:21`](../../../../tests/slice/test_every_enrolled_project.py)
- setup and doctor agree on a two-project machine — the disagreement that exposed all of this.
  [`test_first_run.py:329`](../../../../tests/slice/test_first_run.py#L329)
