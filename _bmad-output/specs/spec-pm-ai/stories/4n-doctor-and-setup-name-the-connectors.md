---
title: 'pm-ai doctor and pm-ai setup name the connectors enrolled on this machine'
type: 'feature'
created: '2026-10-09'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pm-ai setup` can end "healthy" on a machine that will never harvest anything, and `pm-ai doctor` cannot tell the difference either.

Setup closes with a health report, and `doctor` prints the same one: the master key, the project list, `config.toml` — and nothing about connectors. A machine with nothing to harvest from reads exactly like a ready one. The one command that lists connectors, `pm-ai connector check`, contacts each provider to do it, which is the wrong tool when the network is what is broken. AD-39 (revised 2026-10-09) splits the two: `doctor` lists the enrolled connectors without contacting anything; `pm-ai connector check` and the briefing are what surface an unhealthy one.

**Approach:** The health report gains a `connectors` line that names the connectors enrolled on this machine without contacting any of them, or says plainly that none is and how to add one. Both `doctor` and setup's closing report carry it, so neither can end without that sentence.

## Boundaries & Constraints

**Always:**
- **The line contacts nothing.** It reads the enrolment rows under `connectors/` by the one shared rule — an enabled row naming a system pm-ai has a connector for, with a text name — and reports the names, sorted, with the count: "1 enrolled: graph:work", "2 enrolled: gitlab:alpha, graph:work". The live check stays `pm-ai connector check`'s.
- **What the line promises is enrolment, not that the next start builds the row.** Whether a listed row builds a connector is the start's business and is reported there; the line says which rows are enrolled and enabled.
- **Nothing enrolled is an ordinary state, said with its remedy.** With no rows the line is OK, "none enrolled", and names both `pm-ai connector add graph <name>` and `pm-ai connector add gitlab <name>`. With only disabled rows it says "none enabled; 1 disabled: graph:work" — never "none enrolled". A disabled row, or one naming a system pm-ai does not know, is counted that way, not listed as enrolled.
- **OK whenever the directory can be read and every row is well-formed.** A row that cannot be read or parsed is counted and named as a problem on the line, a warning, never silently dropped; the other rows still appear. Two files whose rows carry the same name are one malformed-row problem naming both files, not two enrolled connectors. A `connectors/` directory that cannot be listed is a failing line naming the error — never "none enrolled". Like every other probe, a warning or failure here changes the verdict; setup's exit code still comes from the probe report, as `4h` decided.
- **No secret appears in the report.** Names only; neither the sealed credential store nor the keychain is opened to produce the line.
- **Setup and doctor agree.** Both reports carry the same line, read the same way, so the machine setup just finished and the machine `doctor` describes are one machine — including a machine with two projects run from a folder inside neither, where no daemon can be started.
- **The line needs no daemon**, and sits after the project-list line and before `project selection`.
- **`doctor` and `connector check` count the same rows** (AD-39, 2026-10-10). Both read the enrolment rows by the one rule above; `connector check` adds, for each row, whether the daemon built it and, if not, the reason (no project enrolled; a settings key missing — the listing `4o` gave it), so the two counts are one count and any difference between the commands is a stated reason, never a row one of them left out.

**Ask First:** Nothing.

**Never:**
- No connector health in `doctor`; nothing in the health report contacts a provider or imports its client.
- No change to the existing seven probes or their order. `connector check`'s listing of unbuilt rows with their reason is `4o`'s; this slice asserts the counts agree and changes nothing else it prints.
- No setup step that adds a connector — that is `4p`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| `doctor`, a Graph and a GitLab row | both enabled | `connectors` OK: `2 enrolled: gitlab:alpha, graph:work`; no provider contacted; verdict unchanged | N/A |
| `doctor`, one row | `graph:work` enabled | `connectors` OK: `1 enrolled: graph:work` | N/A |
| `doctor`, none enrolled | no rows | `connectors` OK: none enrolled; remediation names `pm-ai connector add graph <name>` and `pm-ai connector add gitlab <name>`; verdict unchanged | N/A |
| `doctor`, only disabled rows | one row, `enabled: false` | `connectors` OK: `none enabled; 1 disabled: graph:work`; never "none enrolled" | N/A |
| `doctor`, a disabled or unknown-system row beside an enabled one | `enabled: false`, or `system: "jira"` | not listed as enrolled; the enabled row is | N/A |
| `doctor`, a row that cannot be read or parsed | a file in `connectors/` that is not valid JSON, or not readable | `connectors` WARNING naming that file; the other rows still listed; verdict follows the warning | exit from the verdict |
| `doctor`, two files with the same name | two rows both naming `graph:work` | one malformed-row problem naming both files; not listed as two connectors | exit from the verdict |
| `doctor`, `connectors/` cannot be listed | the directory unreadable | `connectors` FAILING naming the error; never "none enrolled" | exit from the verdict |
| `doctor`, two projects, run from outside both | no daemon can be started here | the line still lists the rows | N/A |
| `doctor` with the runtime packages absent | a Graph row, Microsoft's library not installed | still listed; the line never imports or calls the provider | N/A |
| `doctor`, nothing enrolled at all | no project, no rows | the line says none enrolled; the project-list probe still names `project add` first | N/A |
| `doctor` or setup, a sealed sign-in on disk | a Graph row and its sealed token | the token appears nowhere in the output; the keychain is never asked for the key | N/A |
| Setup, clean machine | three steps answered | closing report says none enrolled and names both add commands; exit from the probe verdict as today | N/A |
| Setup, a connector enrolled | `graph:work` on disk, steps re-run | closing report lists `graph:work` | N/A |
| The line's position | any report | after the project-list line, before `project selection` | N/A |
| `doctor` beside `connector check`, a row the daemon cannot build | a GitLab row, no project enrolled | `doctor`: `1 enrolled: gitlab:x`; `connector check`: the same one row, enrolled, not built, with the reason; the counts agree | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/app/entry.py:985-1012`, `:1019` -- `_diagnose` appends `_selection_probe` after `run_all` (1009): the precedent for a probe built where the composition root knows the answer. Append the connector probe the same way, after the registry probe, in `_diagnose` and `_FirstRun.diagnose`; `run_all` is unchanged, so `test_doctor.py`'s seven-probe counts (293, 482, 648) stand
- `pm_ai/app/entry.py:289-344` -- `_FirstRun.diagnose` (344) is `run_all` over a fresh `bootstrap`; it gains the appended line
- `pm_ai/app/entry.py:816-858` -- `_Composition` gains the enrolled names; every construction in `_compose` (858-983) passes them
- `pm_ai/app/wiring.py:1409-1474` -- `Bootstrap`/`bootstrap`, the one read of application artifacts before any daemon, gains the connector reading (no default: a caller that did not read must not report "none"). `tests/slice/test_connector_add_graph.py:220` and `tests/surfaces/test_cli_subcommands.py:57` (`enrolled()`, 79) both build a `Bootstrap` by hand
- `pm_ai/app/wiring.py:624-700` -- `_enrolled_connectors`' row rule (string instance and system, `enabled` exactly `true`, system `gitlab`/`graph`; 683-695) over `_enrolled_configurations` (1380, which swallows a listing failure as `()` and skips unreadable and malformed rows — the probe needs each of those named, so the shared reader returns accepted rows, disabled rows, and problems, and the build keeps iterating the accepted ones). Factor the rule into one reader (`8k`: one list, one rule). The registry refuses a duplicate instance at registration (`pm_ai/connectors/registry.py:151-153`); the reader reports it before any build
- `pm_ai/platform/doctor.py:443-494` -- `registry_readable`'s OK wording (494, `N project(s) enrolled: …`) is the model. `platform` may import neither `connectors` nor `storage`, so `connectors_enrolled(...)` takes the reader's result as a value, as `config_readable` takes bytes
- `pm_ai/domain/health.py:58`, `:72`, `:76` -- `Probe` (an OK probe may carry a remediation, as `_selection_probe` does), `Report`, and `healthy`, which a WARNING fails — hence the verdict follows the line
- `pm_ai/connectors/registry.py:164` -- `instances()` stays, docstring corrected: `doctor` reads the rows, and this method keeps test callers only
- `tests/slice/test_first_run.py:135`, `:329` -- the `reports` fixture records `run_all`'s return, which will not carry the appended line: assert the line on printed output; the two-projects agreement test is where setup and doctor are compared
- `pm_ai/app/entry.py:222-259` -- `_probe_connectors`, where `4o` lists the unbuilt rows beside the probed ones; the agreement test reads both commands' output and compares the counts, so neither command reads the other's list
- `tests/slice/test_connector_add_graph.py:160`, `:208`, `:749` -- `Keychain` (its `fetch`, 174, is what the no-keychain test asserts unused), `machine` fixture and the fresh-`build()` test, the model for a machine with a Graph row

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/app/wiring.py` -- one reader for the rows, returning accepted names, disabled names, and named problems (unreadable, unparseable, duplicate name, directory unlistable); `_enrolled_connectors` iterates its accepted rows; `Bootstrap` carries the result -- one list, one rule
- [ ] `pm_ai/platform/doctor.py` -- `connectors_enrolled(...) -> Probe`: OK with "N enrolled: …" (singular "1 enrolled"), OK "none enabled; N disabled: …", OK "none enrolled" with both add commands as remediation, WARNING naming each problem row, FAILING naming a listing error -- wording lives with the other probes
- [ ] `pm_ai/app/entry.py` -- `_Composition` carries the result; `_diagnose` and `_FirstRun.diagnose` append the probe after the registry line -- both reports, one source
- [ ] `pm_ai/connectors/registry.py` -- correct the `instances()` docstring; the method stays for its test callers -- no promise the code does not keep
- [ ] `tests/architecture/test_doctor.py` -- the probe over the reader's result: sorted, singular and plural, both remedies when empty, "none enabled" with only disabled rows, WARNING per problem, FAILING on a listing error, nothing but names in its text -- the probe's own rows
- [ ] `tests/slice/test_first_run.py` -- the closing line in both states on printed output; the two-projects test asserts both commands print the same line; the line's position asserted after the registry line and before `project selection` -- the setup rows
- [ ] `tests/slice/test_doctor_names_connectors.py` -- new, on `test_connector_add_graph.py`'s `machine`: a Graph and a GitLab row listed sorted; disabled, unknown-system, malformed and duplicate-name rows handled as the matrix says; an unlistable directory fails; `msal` made to fail on import and the row still listed; two projects from outside both; with a sealed sign-in on disk, the token absent from `doctor` and setup output and `Keychain.fetch` never called while the line is produced; a GitLab row with no project enrolled, `doctor` and `connector check` both run, asserting the counts agree and `connector check` names the row as not built with its reason -- the doctor rows
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark resolved the `instances()` entry and the "setup has no connector step" entry's closing-report half; the step itself is `4p`

**Acceptance Criteria:**
- Given a clean machine, when setup runs with its three steps answered, then the printed closing report has a `connectors` line saying none is enrolled and naming `pm-ai connector add graph <name>` and `pm-ai connector add gitlab <name>`, and the exit code equals the probe verdict as before.
- Given two projects and a Graph row, run from outside both, when `doctor` and `setup` both run, then both print the same `connectors` line, after the registry line and before `project selection`, and neither prints `FAILING` for it.
- Given a Graph row and `msal` made to fail on import, when `doctor` runs, then the row is listed and the import was never attempted.
- Given a GitLab row and no project enrolled, when `doctor` and `pm-ai connector check` both run, then both count one connector, and `connector check` names the row as enrolled and not built with the reason that no project is enrolled.
- Given any run of `doctor` or setup on a machine with a sealed sign-in, when its output is searched for the token, then it is absent, and the keychain was never asked for the master key to produce the line.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Design Notes

The connector line reads the enrolment rows, not the running system's connector list, deliberately. That list exists only once a daemon is composed, and the two places the line matters most have none: setup's closing report, which re-reads the machine after setup changed it, and a doctor run on a two-project machine from a folder inside neither. A line saying "none" there would be false. The rows are read by the rule the build applies, so the two cannot drift on which rows count; what the line says is "enrolled", and `connector check` still says "answering". Whether an accepted row then builds — its app id, tenant and widths — is judged at the start, which names the key; the deferred-work entry on that gap stands.

## Verification

**Commands:**
- `uv run pytest tests/architecture/test_doctor.py tests/slice/test_first_run.py tests/slice/test_doctor_names_connectors.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
