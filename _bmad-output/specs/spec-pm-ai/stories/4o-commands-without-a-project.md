---
title: 'Personal and application commands run with no project enrolled'
type: 'bugfix'
created: '2026-10-09'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** On a machine with no project enrolled, pm-ai refuses commands that have nothing to do with a project. Measured in a fresh home: `pm-ai dashboard` (the personal dashboard), `pm-ai config show`, `pm-ai key enrol` and `pm-ai connector add` all exit 3 saying no project is enrolled; `pm-ai goal set` is refused the same way once it reaches a terminal. None of these acts in a project. Only `pm-ai connector check` answers, because story 8k special-cased it.

The architecture settled this (AD-11): a command not aimed at a project is never refused for the lack of one. Story 4m built the selection order, but pm-ai still answers "nothing is enrolled" before it reads what the command is aimed at.

**One earlier decision is touched.** Story 4l's frozen row "Nothing enrolled yet — unchanged: say nothing is enrolled and how to enrol" is renegotiated for commands aimed at the person or at pm-ai itself; it still holds for a command aimed at a project. The 4m review left this for Andrei; **he confirmed "run them" on 2026-10-09**, so the renegotiation of story 4l's row is settled.

**Approach:** With nothing enrolled, a command aimed at the person or at pm-ai itself runs, on a daemon that watches no project. A command aimed at a project is refused as today. This slice lands before `4n`: "doctor and setup unchanged" below means unchanged by this slice; `4n` then adds its connectors line.

## Boundaries & Constraints

**Always:**
- **A command aimed at the person or at pm-ai itself runs with nothing enrolled.** It watches no project and has no built-in connector; what it writes lands in the personal tree or pm-ai's own tree exactly as on a machine with projects.
- **A command aimed at a project is still refused**, with today's sentence: nothing is enrolled, run `pm-ai project add <path>`. Exit 3.
- **Only a missing or empty list of enrolled projects counts as "nothing enrolled".** A list that cannot be read or parsed is a fault, and every daemon-backed command stays refused with that fault's own reason, as today.
- **A fault elsewhere is still named as itself.** A settings file (`config.toml`) that cannot be read or parsed, or a pm-ai directory that cannot be read or written, refuses the command naming that fault, as on a machine with projects; "nothing enrolled" never speaks over it.
- **Every other check still runs first.** `goal set` without a terminal is refused for the terminal before anything else, as today.
- **A master key is not needed for the dashboard.** On a fresh home with no key, `pm-ai dashboard` still runs: its file is plaintext and the key is fetched only when something sealed is written. `connector add` on that home is refused where it seals the credential, as today.
- **`doctor`, `setup` and `project add` are unchanged by this slice.** `doctor`'s check of the enrolled-projects list still reports none enrolled with how to enrol, and still prints no `project selection` line.
- **`connector check` with nothing enrolled still says no connectors are registered and exits 0** — now because the daemon it probes has none. A connector that was enrolled and built is listed and probed, and the exit code follows the reported health: 0 when healthy.
- **`connector check` reports every enrolled row, not every built connector** (AD-39, 2026-10-10). A row the daemon did not build is listed with the reason it was not built — no project enrolled; a settings key missing — never omitted, so `doctor`'s count of enrolled connectors (`4n`) and `connector check`'s count are one count, and any difference between the two commands is a stated reason. An unbuilt row is listed, not probed, so it changes no exit code: the exit code still follows the probed connectors' health.
- **`connector add` with nothing enrolled runs, and what follows is stated rather than hidden.** Enrolment asks for no project. A GitLab connector is saved but not built at the next start — pm-ai prints a warning naming that no project is enrolled, again on every daemon-backed command until one is — then is built into the only project once one is enrolled, as today. A Graph connector is saved and built at the next start; with no project to map Outlook categories to, every meeting is personal, and a mapping that names a project is refused as today.
- **No exit code changes.**

**Ask First:** Nothing — Andrei confirmed on 2026-10-09 that these commands run with nothing enrolled (the alternative, keeping the refusal and narrowing AD-11, was declined).

**Never:**
- No new command, no new option, and no change to AD-11's selection order for a project target.
- No project discovery, no scheduler, no change to how often anything is checked.
- No new refusal for `connector add` when nothing is enrolled; its behaviour is stated above, not changed.
- No probing of a row that was not built, and no exit code from one.
- No change to what `setup` or `doctor` print beyond what the Always clauses name.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Personal dashboard, nothing enrolled | `pm-ai dashboard` | personal dashboard written; exit 0 | N/A |
| Goal set, nothing enrolled | `pm-ai goal set` at a terminal | goal written to the personal goals file; exit 0 | N/A |
| Goal set, no terminal | `pm-ai goal set` from a pipe | refused for the terminal before anything else, as today | exit 3 |
| Config show, nothing enrolled | `pm-ai config show` | every setting printed; exit 0 | N/A |
| Key enrol, nothing enrolled | `pm-ai key enrol` | exit 0; the line naming the keychain entry the key was stored under | its own refusals, unchanged |
| No master key, fresh home | `pm-ai dashboard`; then `pm-ai connector add graph graph:work` | dashboard written, exit 0; `connector add` refused at the sealed write, naming `pm-ai key enrol` | as today |
| Named project, nothing enrolled | `pm-ai dashboard --scope project:alpha` | refused: nothing is enrolled, run `pm-ai project add <path>` | exit 3 |
| Doctor, nothing enrolled | `pm-ai doctor` | enrolled-projects check `absent`, "no project is enrolled"; no `project selection` line; exit 4 as today | N/A |
| Connector check, nothing enrolled, nothing added | `pm-ai connector check` | "no connectors are registered"; exit 0 | N/A |
| GitLab connector added, nothing enrolled | `pm-ai connector add gitlab gitlab:x`, then two daemon-backed commands | enrolled; each start prints the warning that it was not built because no project is enrolled; `connector check` lists it as enrolled, not built, with that reason; exit 0 | N/A |
| Graph connector added, nothing enrolled | `pm-ai connector add graph graph:work`, then `connector check` | enrolled; built at the next start; listed and probed; exit follows the reported health, 0 when healthy | N/A |
| Graph row one settings key short, nothing enrolled | a Graph row with no app id, then `connector check` | listed as enrolled, not built, naming the missing key; nothing probed for it; exit 0 | N/A |
| Enrolled-projects list unreadable or malformed, personal target | `pm-ai dashboard` | refused with the list's own reason | exit 3, as today |
| Settings file or pm-ai directory fault, personal target | `config.toml` unreadable or unparseable; or `~/.pm-ai` cannot be read or written | refused naming that fault; never "nothing enrolled" | exit 3, as with projects |
| A project enrolled afterwards | `pm-ai project add`, then `pm-ai dashboard` | the daemon watches it; a built-in connector exists for it | N/A |

</frozen-after-approval>

## Code Map

Verified against `ddcb9aa` on 2026-10-09.

- `pm_ai/app/entry.py:142` -- `main` composes with `parsed.target`, parsed first (story 4m)
- `pm_ai/app/entry.py:893-905` -- `_compose`: `if not projects:` returns the registry failure before `target` is consulted — the bug. `nothing_enrolled` is set only when the probe is `ABSENT` (904): the condition under which a personal or application target composes instead
- `pm_ai/app/entry.py:948-981` -- the root `OSError` branch (948) and the two `config_readable` returns (973, 981); once composition continues past the empty registry they answer for a personal target — the "fault elsewhere" row
- `pm_ai/app/entry.py:575-596` -- `_select`: a non-project target is `NOT_NEEDED`; a project target goes to `_acting_project(named=…)` (598), whose `_not_enrolled` sentence (778) lists enrolled projects — with none, the registry probe's sentence is the right one, so keep the failure return there
- `pm_ai/app/entry.py:816-856` -- `_Composition`; `nothing_enrolled` (849) exists for `_probe_connectors` (222-259), whose empty-report branch (256) becomes unreachable once `connector check` always has a daemon — retire both or say why not
- `pm_ai/app/entry.py:1019-1050` -- `_selection_probe`: `None` with no selection and no `undecided`, which the unchanged `doctor` path still produces
- `pm_ai/app/wiring.py:223-407` -- `build(root, acting, *, watched=…)`: `_watched` (409) returns `{}` for a non-project scope with `watched=()`; the resolve loop (327), the built-in loop (360) and `Daemon.__post_init__` (185-209) accept that. Nothing new to build; assert it
- `pm_ai/app/wiring.py:752-799` -- `_enrolment_scope`: zero watched projects falls to the "neither" branch (793-797) — reword for the zero case. It runs at every build, which is why the warning repeats. Its reason goes to `_unplaced` (801-811) and `_graph_connector`'s to `_unbuilt` (1323-1333), both stderr-only today; the build must also keep each unbuilt row's name and reason so `connector check` can list it
- `pm_ai/app/wiring.py:813-937` -- `_graph_connector`: builds with empty categories; its `registered` parameter is `_registrar` (588), `False` for every id on an empty registry, so a category naming a project is refused (`UnknownProject`)
- `pm_ai/app/wiring.py:1421-1475` -- `bootstrap`: an empty mapping for no file, an empty file, and a file that would not parse; the `ArtifactState` carries the difference
- `pm_ai/platform/doctor.py:443-462` -- `registry_readable`: `ABSENT` for no file or no entries, with the `pm-ai project add` remedy
- `pm_ai/surfaces/cli/dispatch.py:517-541` -- `require_daemon`; its docstring says "a machine with no project enrolled is an incomplete setup" — correct it, and the same claim in `_connector_check` (971) and `_probe_connectors`. `:1530` `goal set`'s TTY check, before `require_daemon`; `:956` `connector add`'s no-master-key refusal; `:653-657` `key enrol`'s closing line naming the keychain entry
- `pm_ai/surfaces/cli/dispatch.py:1583-1655` -- `TABLE` targets: `dashboard` (1588), `key enrol` (1605), `config show` (1615), `goal set` (1636), `connector add` (1647), `connector check` (1652); `doctor`, `setup`, `project add` have none
- `tests/slice/test_every_enrolled_project.py:398` -- `test_nothing_enrolled_is_unchanged` (no target: still true, rename to say so); `:558` `test_no_selection_line_at_all_when_nothing_is_enrolled`; `:605` the story-4m rows; `:892` a non-project target watches every project; `machine` fixture (141)
- `tests/architecture/test_doctor.py:731` -- `test_an_absent_registry_is_a_first_run_and_names_project_add`; `:738` its empty-registry twin
- `tests/surfaces/test_cli_subcommands.py:181` -- `unregistered` fixture does **not** redirect `HOME` as `registered` (173) does; once a daemon is built for it, it must, or tests write under the real `~/.pm-ai`. `:374` `key enrol` refused for no project (becomes a run); `:578` `connector check` exits 0 (stays)
- `_bmad-output/implementation-artifacts/deferred-work.md:877-880` -- the entry this closes

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/app/entry.py` -- in `_compose`, when the registry is absent or empty and the target is personal or application, build with `watched=()`; keep the failure return for no target, a project target, and any other registry state; the root and config branches answer as they do with projects; retire `nothing_enrolled` and the empty-report branch of `_probe_connectors` if nothing reaches them -- the target is read before "nothing enrolled" is answered
- [ ] `pm_ai/app/wiring.py` -- reword `_enrolment_scope`'s zero-project sentence; assert `build` with a non-project scope and no watched project -- the daemon that runs these commands
- [ ] `pm_ai/app/wiring.py`, `pm_ai/app/entry.py`, `pm_ai/surfaces/cli/dispatch.py` -- the build records every enrolled row it did not build with its reason (the sentence `_unplaced` or `_unbuilt` prints), the daemon carries them, and `_probe_connectors` and `_connector_check` list each beside the probed connectors as "enrolled, not built: <reason>", contributing no health verdict -- one count with `doctor`
- [ ] `pm_ai/surfaces/cli/dispatch.py`, `pm_ai/app/entry.py` -- correct the docstrings that say no project means no daemon -- they would be false
- [ ] `tests/` -- one test per matrix row through `entry.main` under a temporary home: the piped `goal set`, the keychain fake with no key, the config and root faults, two starts printing the GitLab warning twice, the Graph `connector check` exit code, the unbuilt GitLab row and the key-short Graph row each listed by `connector check` with their reason and exit 0; rename `test_nothing_enrolled_is_unchanged`; turn the `key enrol` refusal into a run asserting exit 0 and the keychain-entry line; update the `unregistered` fixture's docstring -- the matrix is the contract
- [ ] `_bmad-output/specs/spec-pm-ai/stories/4l-every-enrolled-project.md` -- append a dated Spec Change Log entry: the nothing-enrolled row is renegotiated by 4o for non-project targets -- the frozen row must not read as still true
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the 4m-review entry resolved

**Acceptance Criteria:**
- Given a fresh home with no project enrolled, when `pm-ai dashboard` runs, then it exits 0 and the personal dashboard file exists.
- Given the same machine, when `pm-ai dashboard --scope project:alpha` runs, then it exits 3 and the message says nothing is enrolled and names `pm-ai project add`.
- Given the same machine, when `pm-ai doctor` runs, then the enrolled-projects check reports none enrolled with the `pm-ai project add` remedy and no `project selection` line is printed — `tests/architecture/test_doctor.py:731` and `tests/slice/test_every_enrolled_project.py:558` stay green unchanged.
- Given a GitLab connector enrolled and nothing else, when two daemon-backed commands run, then each prints the not-built warning naming that no project is enrolled.
- Given that same machine, when `pm-ai connector check` runs, then the GitLab row is listed as enrolled and not built, with the reason that no project is enrolled, and the exit code is 0.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

**Manual check:**
- `HOME=<scratch> PM_AI_DISABLE_ENCRYPTION=1 .venv/bin/pm-ai dashboard` in an empty scratch home -- expected: exit 0 and the written path printed; then `config show` exits 0 and `dashboard --scope project:alpha` exits 3.
