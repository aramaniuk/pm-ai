---
title: 'pm-ai connector add graph produces a working Graph connector'
type: 'feature'
created: '2026-10-09'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'f6ca10a1d20984507572db5569a3d2be762044eb'
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** There is no way to add a Microsoft Graph connector through pm-ai itself.

`pm-ai connector add graph <name>` is refused outright: pm-ai has no check for a Graph credential. And even past that, the entry it writes would be missing the settings the Graph connector needs — the Microsoft app it signs in through, and how far back and how wide each calendar read is — so pm-ai would never build the connector. Today a Graph connector exists only if someone writes both the encrypted credential and its settings file by hand.

A Graph credential is also not something you type. It comes from Microsoft's sign-in: pm-ai shows a web address and a short code, you approve it in a browser, and Microsoft hands back a long-lived sign-in token. That takes minutes, not seconds.

**Approach:** `pm-ai connector add graph <name>` asks for the settings, runs Microsoft's sign-in, checks the result works, and only then saves the encrypted sign-in and the settings together. A name already in use is refused before anything is asked.

## Boundaries & Constraints

**Always:**
- **Refuse a name already in use before asking anything**, so nobody completes a sign-in only to be told the name was taken.
- **It needs a terminal**, as adding a credential does today; without one it is refused before anything is asked.
- **The questions, in order:** the Microsoft app (client) id, required; the tenant, where Enter keeps `organizations`; the harvest window in minutes, which states its 240-minute minimum and has no default; the first-run reach-back in minutes, which must be at least the window. An answer that would not build a connector is asked again with the reason, never saved.
- **Outlook category → project mapping is not asked.** The closing message names the settings file where it is added by hand.
- **The sign-in shows Microsoft's own address and code**, waits for the person, and reports a declined, partial or expired sign-in by its own reason, saving nothing.
- **After sign-in, a live health check runs within 10 seconds** (CAP-35); a failure saves nothing.
- **The sign-in and the settings are saved together**, the same way adding a credential is today: encrypted sign-in under the shared credentials lock, settings file at `0600`. Nothing half-saved is left looking healthy.
- **The sign-in token never appears** on screen, in logs, or in error messages.
- **The connector becomes active at the next start**, as today.

**Ask First:** Any change to the questions asked or their order. Any default for the harvest window or reach-back.

**Never:**
- No re-sign-in for a connector already enrolled, no GitLab probe, no `pm-ai setup` step — each is its own slice (recorded 2026-10-09).
- No command-line options for these settings.
- No built-in Microsoft app id.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | new name, valid answers, sign-in approved | settings and encrypted sign-in saved; "enrolled and healthy; active at the next start"; names the settings file for categories | N/A |
| Name in use | `graph:work` already enrolled | refused before any question | existing duplicate refusal, exit 3 |
| No terminal | stdin not a TTY | refused before any question | exit 3 |
| Window under 240 | `120` | asked again, saying the minimum | N/A |
| Reach-back under window | window 480, reach-back 60 | asked again, saying it must be at least 480 | N/A |
| Blank client id | Enter | asked again | N/A |
| Tenant left blank | Enter | `organizations` saved | N/A |
| Sign-in declined / expired | the person refuses, or the code runs out | that reason; nothing saved | exit 3 |
| Health check fails | sign-in works, check fails or exceeds 10 s | the check's reason; nothing saved | exit 3 |
| Runtime packages missing | `msal` not installed | names the missing runtime extra | exit 3 |
| The new connector builds | after a successful add, pm-ai starts | a Graph connector for that name is in the daemon's connectors | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/surfaces/cli/dispatch.py:609` -- `_connector_add`: `(system, instance)` positionals; `isatty` refusal (626); `getpass` for the credential; `enrol_connector(..., probe=context.probe_credential)`; exceptions → `Refusal`. A `graph` system takes a different path (questions + sign-in) instead of `getpass`
- `pm_ai/surfaces/cli/dispatch.py:271`, `:355` -- `_no_probe` default; `Context.probe_credential` (`CredentialProbePort`). A sign-in callable is injected the same way; surfaces may not import `pm_ai.connectors`
- `pm_ai/app/entry.py:151`, `:170` -- `probe_credential=` injection; `onboard=lambda …` as the pattern for an injected callable
- `pm_ai/core/connector_enrolment.py:214-300` -- `enrol_connector`: name check, duplicate check (connectors/ then sealed store), probe (279, outside the claim), sealed write under `exclusive(config.json)`, then `connectors/<instance>.json` = `{instance, system, enabled, project}`; `OrphanedCredential` (131) if the second write fails. It must accept the Graph settings for the row, and offer the duplicate check on its own so it can run before the questions
- `pm_ai/app/wiring.py:795` -- `_graph_connector` reads `client_id`, `window_width_minutes`, `first_run_reach_back_minutes` (required), `categories`, `tenant` (optional, default `organizations`) from the row; keys at `pm_ai/connectors/graph/__init__.py:218-222`; `WindowPolicy` (`graph/calendar.py:535`) refuses a window under 240 and caps at `MAX_SETTING_MINUTES`. Reuse these checks for the answers rather than restating them
- `pm_ai/connectors/graph/auth.py:652` -- `GraphDeviceCodeAuth.sign_in(present) -> str`: `present` receives Microsoft's sentence with address and code (`_prompt`, 1023); blocks until approval or expiry; **writes into its own store**, so the add path gives it an `InMemoryRefreshTokenStore` (402); errors `AuthTimedOut`, `AuthDeclined`, `GraphUnreachable`, `InteractionRequired`, `GraphAuthError` (missing `msal`)
- `pm_ai/connectors/graph/auth.py:783` -- `check_health`: forced silent refresh, unbounded on its own; bound it with `registry.run_bounded(…, timeout=CREDENTIAL_PROBE_SECONDS)` as `probe.py` does
- `pm_ai/connectors/probe.py` -- `PROBES = {"gitlab": _gitlab}`, `CREDENTIAL_PROBE_SECONDS = 10.0`; its "holding the claim" comment is stale (the probe runs outside it) — correct it
- Tests: `tests/surfaces/test_cli_subcommands.py:831-915` (connector add rows; monkeypatch `isatty`, `getpass`, `cli.enrol_connector`); `tests/core/test_connector_enrolment.py`; `tests/connectors/test_probe.py:38` (`test_no_probe_returns_success_today` — revisit if `PROBES` changes); `tests/connectors/test_graph_auth.py:88` `FakeMsal` (inject via `client_factory`; `msal` is not installed in the test environment)

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/core/connector_enrolment.py` -- a duplicate check callable on its own; enrolment writes a row's extra settings when given them -- one writer for both files
- [x] `pm_ai/app/entry.py` / `pm_ai/app/wiring.py` -- inject a Graph sign-in callable: builds the auth from the answers with an in-memory store, signs in through `present`, then runs the bounded health check; validates answers with the connector's own checks -- the connector layer stays behind the composition root
- [x] `pm_ai/surfaces/cli/dispatch.py` -- the `graph` path: duplicate check, terminal check, the four questions with re-asking, sign-in, enrolment with settings, closing message naming the settings file -- the command
- [x] `pm_ai/connectors/probe.py` -- correct the stale claim comment
- [x] `tests/` -- one test per matrix row, including a full add followed by a fresh `build()` that holds the new Graph connector -- the matrix is the contract
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the two `connector add graph` entries resolved (`:559-561` for its Graph half, `:618-620`)

**Acceptance Criteria:**
- Given a machine with a project enrolled and no Graph connector, when `pm-ai connector add graph graph:work` runs with valid answers and an approved sign-in, then a fresh start builds a Graph connector named `graph:work` and `pm-ai connector check` lists it.
- Given the change, when `uv run pytest` runs, then everything passes with 24 skipped.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass, 24 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Suggested Review Order

**The command**

- The Graph path: up-front checks, four questions with re-asking, sign-in, enrolment, closing message.
  [`dispatch.py:760`](../../../../pm_ai/surfaces/cli/dispatch.py#L760)

- The name, duplicate, git and master-key checks, runnable before any question.
  [`connector_enrolment.py:239`](../../../../pm_ai/core/connector_enrolment.py#L239)

**Sign-in behind the composition root**

- Judges answers with the connector's own checks; signs in into memory; bounded health check; saves what the check left.
  [`wiring.py:953`](../../../../pm_ai/app/wiring.py#L953)

- Microsoft refusing the app or tenant is named, not reported as the network.
  [`auth.py:232`](../../../../pm_ai/connectors/graph/auth.py#L232)

- Tenant shape check, used at the prompt and when a hand-edited row is read.
  [`auth.py:636`](../../../../pm_ai/connectors/graph/auth.py#L636)

**Saving**

- Settings that cannot be written are refused before anything is sealed.
  [`connector_enrolment.py:145`](../../../../pm_ai/core/connector_enrolment.py#L145)

- Redaction covers the raw token, not only its encoded form.
  [`connector_enrolment.py:608`](../../../../pm_ai/core/connector_enrolment.py#L608)

**Tests**

- A valid add saves both files together.
  [`test_connector_add_graph.py:276`](../../../../tests/slice/test_connector_add_graph.py#L276)
