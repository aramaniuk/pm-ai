---
title: 'pm-ai setup offers to add a Microsoft Graph connector'
type: 'feature'
created: '2026-10-09'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A person finishing `pm-ai setup` is never told that a connector exists to add, or that they have none.

Setup walks a new machine through three steps — master key, one project, `config.toml`. Since story 4n its closing report says whether any connector is enrolled, and since story 8l `pm-ai connector add graph <name>` can add a Microsoft Graph connector — but setup never offers it, so the first-run path ends with a sentence telling the person to go and run another command.

**Approach:** Setup gains a fourth, optional step: it asks whether to add a Microsoft Graph connector now and, on "yes", runs exactly the questions, sign-in, health check and save that `connector add graph` runs, through the same code. Depends on `4n` for the closing sentence.

## Boundaries & Constraints

**Always:**
- **The step is offered, never required.** After the config step, setup asks "add a Microsoft Graph connector now? (y/n)". "no" skips it and setup completes; its closing sentence is the connector line's own remedy from `4n`, word for word. **Enter means "no"**: Enter is how a person moves through setup, and "yes" starts a browser sign-in that takes minutes and needs an app id they may not have. Setup already refuses without a terminal before step 1, so the question is only ever asked at one.
- **"yes" runs the `connector add graph` path, not a copy of it:** the connector name is asked, then the same questions, Microsoft sign-in, health check and save, through the same code. Enter keeps `graph:work` when that name is free; when it is already in use the question offers no default and a name must be typed. Setup checks the name once, before handing it to the shared path, so the shared path's own name check cannot then fail. A malformed or in-use name is named and asked again; three refused names end setup at step 4 with exit `3`, naming steps 1–3 as done.
- **A refusal inside the step ends setup as any other step's does**: exit `3`, saying steps 1–3 stay done and `pm-ai setup` or `pm-ai connector add graph <name>` can be run again. A declined, expired or partial sign-in, a failed health check and missing runtime packages all say nothing was saved for the connector. An interruption (Ctrl-C or Ctrl-D) anywhere inside the step — at the y/n question, at any of the connector questions, or during the Microsoft sign-in wait — is this same refusal, naming steps 1–3 as done; the step wraps the whole shared path, so the shared command's own "nothing was saved" interrupt sentence is not what the person sees.
- **A credential sealed but not recorded is said, not hidden.** When the sign-in's token was sealed and the settings row could not be written, the refusal names the sealed entry and says to fix what the write failed on and enrol that name again; it does not say nothing was saved, and the next attempt refuses that name as in use.
- **A machine with an enabled Graph connector reads the step as done**, as an enrolled key does: the names are printed, nothing asked, nothing written. A machine with only a GitLab connector, or only a disabled Graph row, is asked. A second connector is added with `pm-ai connector add graph`.
- **The closing report is `4n`'s**, so it names the connector just added, or says none is. The verdict and exit code still come from the probe report (`4h`): exit 0 when it is healthy, on a first run and a second alike.
- **The sign-in token never appears** on screen, in the closing report, or in a refusal — the shared path's rule, unchanged by being called from here.
- **Setup composes no daemon**, even now that connector commands run without a project (`4o`); the step carries its own storage and sign-in, so a machine whose only project was just registered in step 2 can still reach the sign-in.

**Ask First:** Any change to the questions `connector add graph` asks or their order.

**Never:**
- No GitLab step, no re-sign-in for an enrolled connector, no category-mapping question — each is its own slice (recorded 2026-10-09).
- No change to what `connector add graph` asks, prints or exits with, nor to the three existing steps, their order or their questions.
- No second implementation of the Graph questions or sign-in in setup.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean machine, "no" | three steps answered, `n` | step 4 prints `4n`'s remedy sentence verbatim; setup completes; closing report says none enrolled; exit from the probe verdict | N/A |
| Enter at the question | empty answer | treated as "no" | N/A |
| Clean machine, "yes" | `y`, Enter at the name, valid answers, sign-in approved | `graph:work` saved as `connector add graph` saves it; closing report lists it; exit from the probe verdict | N/A |
| A typed name | `y`, `graph:acme` | saved under that name | N/A |
| Neither y nor n | `maybe` three times | asked again twice, then setup stops at step 4; steps 1–3 stay done | exit `3` |
| One bad name, then a good one | `graph:work` in use; `graph/bad` then `graph:acme` | the first named and asked again; saved under `graph:acme` | N/A |
| Default name in use | `graph:work` enrolled but disabled, `y` | the name question offers no default; Enter is refused as no name and asked again | N/A |
| Three refused names | three malformed or in-use names | setup stops at step 4 naming steps 1–3 as done | exit `3` |
| Sign-in declined, expired or partial | the person refuses, or the code runs out | the shared path's reason; nothing saved; stops at step 4 naming what stays done | exit `3` |
| Health check fails or overruns | sign-in works, check refuses or exceeds 10 s | the check's reason; nothing saved; stops at step 4 | exit `3` |
| Runtime packages missing, "yes" | the runtime extra not installed | names the extra before any connector question; stops at step 4 | exit `3` |
| Interrupted anywhere in step 4 | Ctrl-C or Ctrl-D at the y/n question, at a connector question, or during the sign-in wait | one refusal: steps 1–3 stay done, nothing saved for the connector; the shared command's own interrupt sentence absent | exit `3` |
| Credential sealed, row not written | the sign-in succeeds; the settings directory cannot be written | refusal names the sealed entry for the name and says to fix the write and enrol it again; does not say nothing was saved; a re-run refuses that name as in use | exit `3` |
| Second run, Graph connector enrolled | `graph:work` on disk, enabled | step 4 prints the name, asks nothing; no file's bytes change; exit from the probe verdict (0 when healthy) | N/A |
| Second run, GitLab only or Graph disabled | `gitlab:alpha` only; or `graph:work` with `enabled: false` | step 4 asks | N/A |
| Second run, none enrolled | first run answered `n` | step 4 asks again | N/A |
| Step headings | any run | `[1/4]` … `[4/4]`; step 4 says it is optional and why | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/surfaces/cli/dispatch.py:1442-1494` -- `_setup`: TTY check, config pre-read, `_setup_key` (1488), project, config, closing `first_run.diagnose()` (1493), exit from `report.healthy` (1495). Step 4 goes before the report. `setup` is registered with no `target` (1592-1595), so no daemon is composed for it
- `pm_ai/surfaces/cli/dispatch.py:1212`, `:1237`, `:1368-1382` -- `_ask` (interrupt → refusal naming what stays done, 1230-1234), `_answer` (bounded re-ask, `default`), `_YES`/`_NO` and `_verbose`'s y/n interpreter: reuse, with an empty answer reading as no. Step headings at 1269, 1300, 1399 say `/3`
- `pm_ai/surfaces/cli/dispatch.py:760-869` -- `_connector_add_graph(context, daemon, instance)` reads only `daemon.storage` and `context.graph_sign_in`; take those two as parameters so setup calls the same function. Routed at 724. Its own interrupt text, `_typed` (882-886, "nothing was saved"), and `_enrolment_refusals` (933; `OrphanedCredential` 963) raise `Refusal`; the step catches `KeyboardInterrupt`/`EOFError` around the whole call and wraps every `Refusal` as "setup stopped at step 4", so the orphaned-credential sentence is kept and the interrupt sentence is replaced
- `pm_ai/surfaces/cli/dispatch.py:183-213`, `:271` -- `FirstRun` protocol gains the application-scope `StoragePort`, a `GraphSignIn` over it, and `4n`'s reader result for the already-done check. `Context.graph_sign_in` (451) is `None` on the setup machine, hence `FirstRun` carries its own
- `pm_ai/app/entry.py:289-344` -- `_FirstRun` builds `application_storage` per call; its sign-in is `GraphEnrolment(storage=application_storage(keychain))`, as main binds one at 157
- `pm_ai/app/wiring.py:953-1021` -- `GraphEnrolment`: `default_tenant` (995), `settings_file` (999), `ready()` (1006, the `msal` check), `refusal` (1021) and the sign-in; `application_storage` (1477)
- `pm_ai/core/connector_enrolment.py:239`, `:156`, `:410-417` -- `assert_enrollable` (`MalformedInstanceName`/`DuplicateConnector`, the name re-ask; `KeyNotFound` cannot arise after step 1); `OrphanedCredential` and the sentence it carries, which already names the instance and "enrol again"
- `tests/slice/test_first_run.py:83`, `:182`, `:217`, `:329` -- `Operator` (running out of answers is Ctrl-D: every script needs the step-4 answer), `answers_for`, the `[n/3]` assertion, the two-projects test (its second and third runs reach step 4)
- `tests/slice/test_connector_add_graph.py:73`, `:208`, `:231`, `:566-576`, `:749` -- `Microsoft`, `machine`, `_microsoft` (patches `entry.GraphEnrolment`, which the step's sign-in must go through too), the Ctrl-C-during-sign-in model (`Microsoft(device=KeyboardInterrupt())`), the fresh-`build()` check

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- `_connector_add_graph` takes storage and sign-in; `FirstRun` widened; step 4: done when an enabled Graph row exists, else the y/n question with Enter as no, the name question (default only when `graph:work` is free, checked once with `assert_enrollable`, re-asked up to three times), the shared Graph path, interrupts and refusals wrapped as "setup stopped at step 4", the "no" branch printing `4n`'s remedy; headings `/4` -- the step
- [ ] `pm_ai/app/entry.py` -- `_FirstRun` gains the storage, the `GraphEnrolment` over it, and `4n`'s reader result -- the root supplies what `surfaces` may not reach
- [ ] `tests/slice/test_first_run.py` -- every script gains the step-4 answer; `[n/4]`; "no" (asserting `4n`'s sentence), Enter, neither, second-run-enrolled with exit 0 when healthy, GitLab-only and disabled-Graph asked, second-run-asks-again, interrupted-at-the-question rows -- the setup rows
- [ ] `tests/slice/test_setup_connector_step.py` -- new, reusing `Microsoft` and `machine`: "yes" through to a saved `graph:work` a fresh `build()` holds; a typed name; `test_one_refused_name_is_asked_again_and_the_good_one_is_saved` and `test_three_refused_names_stop_setup_at_step_4`; the default withheld when `graph:work` is in use; declined sign-in, failed check, overrun check and missing runtime packages each stop at step 4 with nothing saved; Ctrl-C at a connector question and during the sign-in wait each give the step-4 refusal without the shared command's interrupt sentence; a sealed credential with an unwritable settings directory names the entry and the next run refuses the name; the token absent from output -- the "yes" rows
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark resolved "`pm-ai setup` has no connector step"

**Acceptance Criteria:**
- Given a clean machine, when setup runs with `y`, Enter at the name, valid answers and an approved sign-in, then `connectors/graph:work.json` and the sealed sign-in exist as `connector add graph` writes them, the closing report lists `graph:work`, and a fresh `build()` holds a Graph connector of that name.
- Given a clean machine, when setup runs with Enter at the question, then nothing is asked about Graph, no connector file exists, the step's closing sentence equals `4n`'s remedy, and setup completes.
- Given `graph:work` enrolled and enabled, when setup runs again on a healthy machine, then no file's bytes differ afterwards and it exits `0` — asserted on file contents, as `4h`'s second run is.
- Given a declined sign-in, or Ctrl-C during the sign-in wait, when setup stops, then no `connectors/` row and no sealed credential exist, the exit code is `3`, and the refusal names steps 1–3 as done.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Verification

**Commands:**
- `uv run pytest tests/slice/test_first_run.py tests/slice/test_setup_connector_step.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
