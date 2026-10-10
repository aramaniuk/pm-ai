---
title: 'pm-ai connector add gitlab produces a working GitLab connector'
type: 'feature'
created: '2026-10-09'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `pm-ai connector add gitlab <name>` always refuses. Its credential check refuses every token on purpose, because until the GitLab transport slice there was no way to ask GitLab anything, and accepting a token would have sealed one nobody checked. Even past the check, the entry it writes has no GitLab host, and the project path (`group/project`) cannot be derived from a name that may not contain a slash. Today a GitLab connector exists only if someone writes the encrypted credential and the settings file by hand.

**Approach:** `pm-ai connector add gitlab <name>` asks for the host and the project path, takes the token at a hidden prompt, asks GitLab for one commit of that project within ten seconds, and only then saves the encrypted token and the settings together — the same shape as adding a Graph connector. Depends on the GitLab transport slice (`8n`), whose health check is what this runs.

## Boundaries & Constraints

**Always:**
- **Refuse what can be known before asking anything, in this order:** the name's shape; a name in use — a settings row on disk, or (once the keychain is reached) a sealed credential with no row, the half-enrolled state, each naming which half exists and the repair; no terminal; the keychain — no master key, or one that cannot be read; and, on a machine watching several projects, a name matching none of them, since such a connector is never built — the message says to name it `gitlab:<enrolled project id>`. A CLI composed with no GitLab check refuses by name before any question.
- **With no project enrolled the add is not refused**: it is saved and not built until a project is enrolled, as the commands-without-a-project slice states.
- **A row named `gitlab:<enrolled project id>` replaces that project's credential-less built-in connector** — the ordinary happy path on a machine with projects, and the name the built-in's own "not set up yet" message tells the operator to use.
- **The questions, in order:** the GitLab host, where Enter keeps `https://gitlab.com` and anything typed must be an https address, optionally with a path (`https://git.internal/gitlab`), with no query, fragment, user info or trailing slash; the project path, required, in the form `group/project` (at least one slash, no leading slash, no `?`, `#` or space). Then the token at a hidden prompt, never echoed, stripped of surrounding whitespace; a blank token is refused, and Ctrl-C or Ctrl-D there ends with "interrupted; nothing was saved". An answer that would not build a connector is asked again with the reason, never saved.
- **The token is a GitLab personal access token with the `read_api` scope.** The prompt says so. Its shape is not checked — self-managed instances set their own prefix.
- **The live check asks for one commit of that project on that host** (https://docs.gitlab.com/api/commits/), bounded by the registry's ten seconds and the transport's thirty-second socket timeout. It is the transport slice's own health check, run behind the composition root the way the Graph sign-in is. Not the signed-in-user call: a token with only the `read_user` scope can read the user endpoints and nothing else (https://docs.gitlab.com/security/tokens/access_token_scopes/), so that check would accept a token every harvest then fails with.
- **Each answer is refused by its own reason**, nothing saved: 401 is a refused token; **403 refuses enrolment**, naming `read_api` as the missing scope — a connector that cannot harvest is never sealed, not even with a warning; 404 is no project visible at that path — the path, not the token, is the likely mistake; 429, 5xx, no bytes within the socket timeout, no answer within ten seconds, or a host that cannot be reached is "unreachable, try later", distinct from "refused", so a good token is not reissued for a typo in the host; a redirect is refused asking for the final address; a 200 whose body is not a list is refused as not a GitLab API answer.
- **The first-run reach-back is not asked.** The transport slice's seven-day constant applies; the settings file may override it by hand with `first_run_reach_back_minutes`.
- **The settings and the token are saved together**, as every enrolment is: encrypted token under the shared credentials lock, settings file at the restricted mode holding `instance`, `system`, `enabled`, the declared `project` path and `host`. Nothing half-saved is left looking healthy.
- **The token never appears** on screen, in logs or in error messages.
- **The closing message** says the connector is enrolled and healthy, becomes active at the next start, and names the settings file.
- **The generic add path keeps working for nothing.** With the GitLab check moved behind the composition root, the probe table that `pm-ai connector add <other>` consults is empty, and an unknown system is still refused by name rather than sealed.

**Ask First:** Nothing. A 403 refusing rather than warning, and the reach-back not being asked, were settled on 2026-10-09 and are stated above.

**Never:** No change to the transport. No command-line options for the host or path. No re-enrolment of a name in use. No `pm-ai setup` step — its own slice. No change to the Graph path.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | `gitlab:alpha` on a machine watching `alpha`; Enter, `acme/web`, good token, 200 | settings and encrypted token saved; "enrolled and healthy; active at the next start"; names the file; replaces the built-in at the next start | N/A |
| Name in use | settings row exists | refused before any question, naming the row | exit 3 |
| Half-enrolled | sealed credential, no row | refused before any question, naming the repair | exit 3 |
| No master key / keychain locked | keychain empty, or unreadable | refused before any question, naming `pm-ai key enrol` or the keychain | exit 3 |
| Name matches no project | two projects watched, `gitlab:other` | refused before any question, listing the names that would build | exit 3 |
| No project enrolled | fresh machine, key enrolled | saved; built once a project is enrolled | N/A |
| No terminal | stdin not a TTY | refused before any question | exit 3 |
| No check supplied | CLI composed without it | refused by name before any question | exit 3 |
| Host blank | Enter | `https://gitlab.com` saved | N/A |
| Host with a path | `https://git.internal/gitlab` | accepted and saved as typed | N/A |
| Bad host | `http://git.internal`, `https://x/?a`, trailing slash | asked again with the reason | N/A |
| Path without a slash, or leading one | `web`, `/acme/web` | asked again, showing `group/project` | N/A |
| Blank token | spaces, then Enter | refused; nothing saved | exit 3 |
| Interrupted at the token | Ctrl-C or Ctrl-D | "interrupted; nothing was saved" | exit 3 |
| Token refused | 401 | "GitLab refused the token"; nothing saved | exit 3 |
| Token lacks scope | 403 | names `read_api`; nothing saved | exit 3 |
| Project not found | 404 | names the path and host; nothing saved | exit 3 |
| Unreachable | 429, 5xx, silent, connection refused | "unreachable, try later", not "refused" | exit 3 |
| Redirect | 302 | refused: give the final address | exit 3 |
| Not an API answer | 200 with `{}` | refused: not a GitLab API answer | exit 3 |
| Other system | `pm-ai connector add jira j:one` | refused by name, as today | exit 3 |
| The new connector builds | after a successful add, pm-ai starts | a GitLab connector named `<name>` with that host and path is in the daemon's connectors; `connector check` lists it | N/A |
| Token on screen | any outcome | the token appears in neither stdout nor stderr | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/surfaces/cli/dispatch.py:703` -- `_connector_add`: the `graph` branch (722-723), `isatty` refusal (727), `getpass` (739), `enrol_connector(..., probe=context.probe_credential)` (743-749). A `gitlab` system takes its own path, like `graph`
- `pm_ai/surfaces/cli/dispatch.py:760` -- `_connector_add_graph`: the shape to copy — up-front checks, `_ask_until` (892) with re-asking, `_typed` (872, whose Ctrl-C/Ctrl-D refusal is reused at the hidden prompt), enrolment with `settings=` and a `probe` that reports the check already run (844-851), the closing message; `_enrolment_refusals` (933)
- `pm_ai/surfaces/cli/dispatch.py:246,271` -- `GraphSignInOutcome` and `GraphSignIn`, the protocols the CLI sees; a `GitLabProbe` protocol joins them. `Context.probe_credential` (440), `graph_sign_in` (451); `main` parameters (1848-1849)
- `pm_ai/app/wiring.py:953` -- `GraphEnrolment`: `settings_file` (999), `ready` (1006), the bounded health check through `run_bounded` (1120-1125); a `GitLabEnrolment` beside it judges the host and path, builds the transport slice's adapter from the answers, runs its health check bounded at `CREDENTIAL_PROBE_SECONDS`, and converts the probe's verdict to `ProbeFailed` / `ProbeUnreachable` (AD-49); `_enrolment_scope` (752) is the placement rule the up-front name check reuses; `Daemon.watched` (`build`, 223) is what it reads
- `pm_ai/app/entry.py:152,157` -- the two injections; a third for GitLab, bound to `daemon.storage` and `daemon.watched`
- `pm_ai/connectors/probe.py:41,59` -- `_gitlab`, the refusal this slice removes, and `PROBES`, which becomes empty; the docstring (9-18) is rewritten to say both systems enrol behind the composition root
- `pm_ai/core/connector_enrolment.py:121,239,282` -- `RESERVED_ROW_KEYS` holds `project`, so the declared path cannot travel in `settings`; `assert_enrollable`; `enrol_connector`, whose row defaults `project` from the instance (361) and calls the probe (375)
- `pm_ai/ports/__init__.py:178` -- `CredentialProbePort`, unchanged
- Tests: `tests/core/test_connector_enrolment.py:957-975` — the clash test, whose `project` case keeps refusing a setting and gains a sibling for the parameter. `tests/connectors/test_probe.py:38` — vacuous over an empty table: deleted; `:19-23` — "gitlab" among the known systems becomes "none". `tests/surfaces/test_cli_subcommands.py:846-887` — the `gitlab:alpha` rows that monkeypatch `getpass` and `cli.enrol_connector`. `tests/slice/test_connector_add_graph.py:1` — the pattern: a real temporary home, a fake keychain, prompts recorded by replacing `input`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/core/connector_enrolment.py` -- a `project=` parameter on `enrol_connector` that overrides the default derived from the instance; `project` stays reserved against `settings` -- the row holds the real path without weakening the clash rule
- [ ] `pm_ai/app/wiring.py` -- `GitLabEnrolment`: `ready(instance)` (keychain, name placement), `refusal(host=, project=)`, `check(instance, host, project, credential)` running the health check under the ten-second bound and returning the settings to save -- the connector layer stays behind the composition root
- [ ] `pm_ai/app/entry.py` -- inject it beside the Graph sign-in -- the CLI may not import connectors
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- the `gitlab` path: up-front checks in order, two questions with re-asking, the hidden prompt with stripping and the interruption refusal, the check, enrolment with `project=` and `settings=`, closing message; the `GitLabProbe` protocol -- the command
- [ ] `pm_ai/connectors/probe.py` -- remove `_gitlab`; rewrite the docstring -- no entry may refuse on purpose once a real check exists
- [ ] `tests/slice/test_connector_add_gitlab.py`, `tests/connectors/test_probe.py`, `tests/core/test_connector_enrolment.py`, `tests/surfaces/test_cli_subcommands.py` -- one test per matrix row, with GitLab faked at the transport (no socket), including a full add followed by a fresh `build()` holding the new connector -- the matrix is the contract
- [ ] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark resolved: the "always refuses" entry (`:908`), the GitLab half of the 33a entry (`:560`), the slash entry (`:504`)

**Acceptance Criteria:**
- Given a machine with project `alpha` enrolled and a master key, when `pm-ai connector add gitlab gitlab:alpha` runs with Enter, `acme/web` and a token the fake GitLab answers 200 for, then the saved row's `project` is `acme/web` and its `host` is `https://gitlab.com`, the sealed store holds the token, and a fresh `build()` has one GitLab connector `gitlab:alpha` — the enrolled one, holding the credential, in the built-in's place.
- Given the fake GitLab answers 403, when the same command runs, then nothing is saved and the refusal names `read_api`.
- Given `uv run pytest`, then all pass and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Design Notes

Row keys this slice writes: `host` and `project` (the GitLab path); `first_run_reach_back_minutes` is hand-added only. `RESERVED_ROW_KEYS` (`connector_enrolment.py:121`) governs clashes, which is why the declared path travels as a parameter rather than a setting. Host rules are the transport slice's: https scheme, optional path, no query, fragment, user info or trailing slash.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_connector_add_gitlab.py tests/connectors/test_probe.py tests/core/test_connector_enrolment.py -q` -- expected: all matrix rows pass, no socket
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
