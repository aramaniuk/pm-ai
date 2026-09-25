# Review — AD-11 selection order, currency / reality check

- **Target:** `ARCHITECTURE-SPINE.md`, AD-11 changed text only (working-tree diff on `architecture/ad-11-selection-order`)
- **Lens:** every decision checked against the web, the existing project, or the starter. This change names no new technology, so the check was against the repo.
- **Date:** 2026-09-25
- **Verdict:** **Revise before merge.** The facts about parsing hold. Two sentences describe as current, or as settled, behaviour that the code does the opposite of today, and the amendment does not mark them as not yet built.

## How this was checked

- Read `pm_ai/surfaces/cli/dispatch.py` (`_scope`, `_dashboard`, `TABLE`, `dispatch`, `_options`, `Context.require_selection` / `require_daemon`).
- Read `pm_ai/app/entry.py` (`main`, `_compose`, `_acting_project`, `_outside_every_project`, the `project selection` probe).
- Read `tests/slice/test_every_enrolled_project.py` and `pm_ai/core/project_registry.py`.
- Ran a throwaway test using the slice's `machine` fixture: two enrolled projects (`alpha`, `beta`), working directory outside both. The test file was deleted afterwards and the tree was left clean.
  - `pm-ai dashboard` → **exit 3**, refusal: "this command was run in …/elsewhere, which is inside none of the 2 enrolled projects…"
  - `pm-ai dashboard --scope project:beta` → **exit 3**, the same refusal.
- `uv run pytest` was not run in full. It was not needed for a document review.

## Claims confirmed

| Claim in amended AD-11 | Evidence |
|---|---|
| `pm-ai dashboard` already parses `--scope project:<id>` | `dispatch._scope` splits on `:`. `project` with no subject → `None` → usage exit 2. `TABLE["dashboard"]` declares `options=(SCOPE_ARGUMENT,)`. |
| With no `--scope`, dashboard targets personal | `_scope(None)` returns `DataScope(ScopeKind.PERSONAL)`. The docstring cites CAP-9. This holds only when composition succeeds. See F1. |
| Folder rule: containment, innermost first, no git; single project binds anywhere; three separate refusals; doctor probe always `OK` | `_acting_project` and its helpers match this exactly (`here.is_relative_to(root)`, max `len(root.parts)`, `_no_working_directory`, `_outside_every_project`, `_one_directory_two_ids`). The probe returns `Health.OK` on both branches. This text is carried over from 2026-09-24 and is accurate. |
| Standing in one project, `--scope project:<other>` writes the other's dashboard | `test_the_cli_renders_the_other_projects_dashboard_into_the_other_projects_tree` passes. |
| No second flag names a project today | The only declared long option in `TABLE` is `scope`. Nothing else accepts `--project` or similar. |

## Findings

### F1 — "renders the personal dashboard (CAP-9) wherever it runs" is false today (high)

`main` builds the composition before it looks at `argv`. On a machine with more than one enrolled project and a working directory outside all of them, `_acting_project` returns undecided. `_dashboard` then calls `context.require_daemon()`, which calls `require_selection()` and raises the outside-every-project refusal. The personal dashboard does not depend on any project, yet it is refused with exit 3 (measured above).

The sentence is written in the present tense, next to a bullet headed "The folder rule is implemented", so a reader will take it as current behaviour. Two ways to fix it:

- mark it as target behaviour for a named later slice, or
- word it as a rule the next slice must satisfy, and name the change it needs: a personal-scope command must not go through the project-selection refusal.

As written, the sentence also sits badly with the existing claim that the three refusal states apply to "every command but `doctor`".

### F2 — "naming one lifts the outside-every-folder refusal" and "a named project wins" describe unbuilt behaviour, and the parser order blocks them (high)

Measured: `pm-ai dashboard --scope project:beta` from outside every folder is refused with exit 3. Selection happens in `entry._compose`, before `dispatch` parses options, so no command-line word can take part in the selection today. The only reason "named wins" appears to work from inside `alpha` is that the dashboard's *write target* comes from `--scope` while the daemon's *acting project* is still `alpha`:

- `daemon.scope.project_id == "alpha"`
- the doctor probe still says "this command acts in alpha, chosen by the working directory"

So step 1 of the selection order is not a current fact in any sense. Building it needs either an early read of `--scope` before `_compose`, or a composition that is deferred until after dispatch has parsed options. That is a change to how the composition root is ordered, and the AD should say so. The amendment also removes the old line "naming a project on the command line is a later slice" without adding a replacement marker. After the edit, nothing in AD-11 says this part is unbuilt.

### F3 — "One spelling: `--scope project:<id>`" cannot yet be accepted by the commands that need it (medium)

`dispatch` parses `--name value` options only on commands that have a `run` and no leaves. Leaf commands (`connector add`, `project add`, `goal set`, `key enrol`, `config show`) get only positional arguments, and extra words are refused as an arity error. `connector add` is the command that most needs a named project: today it binds to the acting project (`test_an_enrolled_connector_files_into_its_own_projects_tree`). It cannot take `--scope` without adding option parsing to the leaf branch.

This is not a reason to reject the rule. The AD should just not suggest that the spelling is already available beyond `dashboard`.

A related gap: `--scope personal` and `--scope people:<id>` also parse. The rule does not say what they mean for step 1. Does `--scope personal` count as "a project named", or as no project named, and does it therefore lift the refusal? F1 depends on the answer.

### F4 — The project alias is a second name for a project, and the rule does not mention it (low)

`project add <path> [alias]` stores a unique `alias` (`project_registry._refuse_colliding_aliases`). `_scope` accepts only the id. This matches "`project:<id>`", so it is not a contradiction. However, "no second flag names the same thing" leaves open whether `project:<alias>` must be refused, accepted, or pointed at the id. One clause settles it.

### F5 — The doctor probe wording will become false under the new order (low; context, not changed text)

`_selection_probe` says "chosen by the working directory" whenever a daemon exists. That is already inaccurate in the single-project case, where the folder plays no part. Under step 1 it would also be wrong whenever a named project decides. AD-11 promises a probe that "says which project this invocation binds to", so the probe will need to say which rule picked the project.

## Not flagged

- No library, framework, or version claims appear in the changed text, so nothing needed checking against the web.
- The line "`Path.cwd()` appeared once in the package" is historical and was not re-verified. It is unchanged text.
