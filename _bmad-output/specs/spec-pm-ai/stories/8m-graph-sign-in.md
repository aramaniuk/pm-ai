---
title: 'pm-ai connector sign-in redoes the Microsoft sign-in for an enrolled Graph connector'
type: 'feature'
created: '2026-10-09'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Graph connector whose sign-in has gone bad cannot be repaired.

When Microsoft rotates a connector's token and pm-ai cannot save the new one, when the token expires or is revoked, when conditional access wants a human, or when a permission is granted after enrolment, every check reports the connector failing, tells the person to redo the sign-in, and admits pm-ai has no command for it: `pm-ai connector add graph` refuses a name already in use. Older messages say "sign in again" or "enrol again" without saying how.

**Approach:** `pm-ai connector sign-in <instance>` reruns Microsoft's sign-in for a connector already enrolled, using the app id and tenant in its settings, checks the result, and replaces the stored sign-in for that connector only. Every remedy that asks for a new sign-in names the command. AD-39 makes re-consent a staged Proposal (AD-13), which story 13 has not built; this command is the interim, run by a person at a terminal. The daemon never prompts on its own. Lands after story 8q, which defines the signed-in user's id, its row key and the one-key row update this command writes through.

## Boundaries & Constraints

**Always:**
- **The command asks nothing but the sign-in.** The app id and tenant come from the connector's settings row, judged before the sign-in by the checks the daemon's start applies: app id present, tenant well-formed, both harvest widths present and valid. A row that would not build is refused naming the key; nothing is asked or saved.
- **It needs a terminal** and an enrolled master key, but no enrolled project, like the other connector commands.
- **Refused by name before the sign-in, saving nothing:** no connector of that name; a row whose system is missing, not text, or not Graph; a sealed entry recorded under another system; runtime packages missing; no master key. A row switched off (`enabled` false) may still be signed in — the switch governs harvesting, not enrolment.
- **The sign-in is the one `connector add graph` built:** Microsoft's address and code on screen; a declined, partial or expired sign-in reported by its own reason; then the bounded 10-second health check (CAP-35). Either failing saves nothing. A check that passes with a warning saves, prints the warning, and exits 0.
- **The signed-in user must be the connector's user.** The sign-in's tenant must match the row's, and the signed-in user must match the user the row records, by 8q's rule (a row with no usable user id accepts any user and records it); either mismatch is refused by name, saving nothing — a different person or tenant is a different connector.
- **The new sign-in replaces this connector's stored one and nothing else**, under the shared credentials lock with the brief wait a rotation write gets; a file still busy after that is refused as busy, by name. An absent entry — none for the name, or no credentials file at all — is written, or the "rotated token had nowhere to go" case would have no remedy.
- **Credential first, then the row.** Under the same lock the row is updated through 8q's one-key writer, so a category mapping hand-added during the sign-in is kept. If the row write fails after the credential was replaced, the command says exactly that — the sign-in is replaced, the user id was not recorded, re-running records it — and exits 3. The closing message (sign-in replaced; check passed or warned; no restart needed, a running daemon reads the new sign-in on its next refresh) is printed only when both landed.
- **Every remedy that asks for a new sign-in names `pm-ai connector sign-in <instance>`**: a stale, revoked or never-completed credential, conditional access, a permission granted late, a rotated token that could not be saved, an account that cannot be identified, a request Graph refused twice. "Sign in again", "enrol again" and "pm-ai has no command" no longer appear.
- **The sign-in token never appears** on screen, in logs or in error messages.

**Ask First:** Nothing. There is no `--tenant` or `--client-id` option: a different app or tenant is a different connector, and the row is hand-editable.

**Never:**
- No change to the credentials file's layout or the sealed sign-in's shape.
- No Proposal, no Telegram surface, no daemon-side prompting — story 13's.
- No change to the four questions `connector add graph` asks; no GitLab sign-in; no `pm-ai setup` step.
- No change to the master-key wording in `pm_ai/core/enrolment.py` ("enrol again" there means `pm-ai key enrol`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | `graph:work` enrolled, token retired, sign-in approved | address and code shown; check passes; stored sign-in replaced; the row records the signed-in user (8q); every other row key and every other connector unchanged; "sign-in replaced; healthy"; exit 0 | N/A |
| No project enrolled | key enrolled, no project | runs as on any machine | N/A |
| Refused before anything | no row named `graph:nope`; no terminal; no master key; runtime packages not installed | the reason by name (`pm-ai key enrol`, the runtime extra); nothing shown or saved | exit 3 |
| Wrong or missing system | `gitlab:alpha`; a row with no `system` or one that is not text; row says graph, sealed entry says gitlab | refused naming the system(s) or the key | exit 3 |
| Disabled row | `enabled: false` | signed in and replaced as the happy path; `enabled` untouched | N/A |
| Row that would not build | `client_id` missing or blank; tenant malformed; a width missing or invalid | refused naming the key; nothing asked | exit 3 |
| Sign-in fails | declined, code expired, or a declared permission not granted | that reason; stored sign-in untouched | exit 3 |
| Health check fails or overruns | check fails or exceeds 10 s | the check's reason; untouched | exit 3 |
| Health check warns | check passes with a warning | saved; the warning printed; "sign-in replaced; warned" | exit 0 |
| Credentials file busy | another run holds the lock past the wait | "busy", naming the claim file; untouched | exit 3 |
| Sealed entry or file absent | row present; no entry for the name, or no credentials file | entry written (file created if needed); "sign-in replaced" | exit 0 |
| Different user or tenant | the signed-in user differs from the row's recorded one (8q's comparison); or the sign-in's tenant differs from the row's | refused naming the mismatch; untouched | exit 3 |
| Row write fails after the credential landed | row directory made unwritable after the sign-in | "sign-in replaced; the user id was not recorded; run the command again to record it"; no closing success line | exit 3 |
| Remedies name the command | a check on a stale, interactive, declined, abandoned or unsaved-rotation credential; a request refused twice | each remedy contains `pm-ai connector sign-in graph:work`; no "Sign in again", "enrol again" or "has no command" | N/A |
| Token never on screen | happy path and every refusal | neither the old nor the new token in any output | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/surfaces/cli/dispatch.py:1640` -- the `connector` group: `add` (1643, `takes=("system", "instance")`, `target=_application`), `check` (1649); `sign-in` hangs here with `takes=("instance",)` and `target=_application` (1069), so it runs with no project (story 4o). Exit codes 107-125; `Refusal` 136; `require_daemon` 517
- `pm_ai/surfaces/cli/dispatch.py:760` -- `_connector_add_graph`: wiring-fault refusal 791, `assert_enrollable` 799, `graph.ready()` 800, TTY refusal 802, `graph.sign_in(...)` 833, `enrol_connector(..., settings=)` 840, closing message 853-868 (warned state 857). The new handler is this without the questions and the duplicate check. `_enrolment_refusals` (933) maps `ArtifactBusy` (961) and `OrphanedCredential` (963), not `CredentialNotHeld` — which the handler never meets, since an absent entry is written
- `pm_ai/surfaces/cli/dispatch.py:246`, `:271`, `:451` -- `GraphSignInOutcome`, `GraphSignIn`, `Context.graph_sign_in`: the protocol gains a row reader and a keyed replacement; surfaces import neither `pm_ai.connectors` nor `pm_ai.storage`
- `pm_ai/app/entry.py:157` -- `graph_sign_in=GraphEnrolment(storage=daemon.storage)`
- `pm_ai/app/wiring.py:953` -- `GraphEnrolment`; `sign_in` 1067: WARNING keeps `healthy=False` and appends the remediation (1159-1164); its `settings` (1165) carry the user id since 8q
- `pm_ai/app/wiring.py:813` -- `_graph_connector`'s row checks, applied before the sign-in: `client_id` 849, `graph_window_policy` 854, `tenant` 881-890. `_enrolled_configurations` 1380 reads the rows
- `pm_ai/app/wiring.py:1200` -- `SealedRefreshTokenStore`: `exclusive()` 1270 waits `CLAIM_WAIT_ATTEMPTS × CLAIM_WAIT_INTERVAL` (1192); `write` 1250 via `replace_credential`; `StoreBusy` names the claim file
- `pm_ai/core/connector_enrolment.py:422` -- `replace_credential`: claim 454, `CredentialNotHeld` (171) for a missing entry (468) or another system (`_sealed_under` 182); gains a mode that writes an absent entry when the caller says the row exists (an absent file decodes to an empty document, `_decode` 548). The row update and the user comparison are 8q's, beside `assert_enrollable` (239)
- `pm_ai/connectors/graph/auth.py:724` -- `sign_in`; the signed-in user's id and tenant (`tid`, 1247) come from 8q's result; `require_msal` 600; `check_health` 869
- `pm_ai/connectors/graph/auth.py` -- remedies to reword: `_REDO_SIGN_IN` 455 (becomes a function of the instance), 783, docstring 876-877, 937 stale, 946-948 interactive, 955 declined, 968 abandoned, 977/987/994, `_cached_account` 1211, `_assert_granted` 1446, `_refuse` 1488. Lines 167 and 518 are commentary and stay
- `pm_ai/connectors/graph/client.py:655-660` -- "Sign in again" after two 401s; `self.auth.instance` names the command; pinned by `tests/connectors/test_graph_calendar_fetch.py:913`
- Tests: `tests/slice/test_connector_add_graph.py` (`Microsoft` 73, `machine` 208, `_microsoft` 231, `_add` 240, `_sealed_token` 254, `_nothing_saved` 261); `tests/slice/test_rotated_tokens_are_kept.py:267-278`, `:329`, `:414`; `tests/connectors/test_graph_auth.py:88` `FakeMsal`, `:1083-1112`; `tests/surfaces/test_cli_subcommands.py:762` leaf list

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/connectors/graph/auth.py`, `client.py` -- every remedy listed, and the 401-twice one, names `pm-ai connector sign-in <instance>` -- the remedies become true
- [ ] `pm_ai/core/connector_enrolment.py` -- `replace_credential` writes an absent entry or file when told the row exists -- one writer for the sealed file
- [ ] `pm_ai/app/wiring.py` -- `GraphEnrolment`: read and judge a row with `_graph_connector`'s checks; a replacement that signs in, checks, compares tenant and (through 8q) user, writes the credential then the row through 8q's one-key update under the waited-for claim, and reports a row write that fails after the credential landed -- the connector layer stays behind the composition root
- [ ] `pm_ai/surfaces/cli/dispatch.py` -- `sign-in` leaf (application target) and handler -- the command
- [ ] `tests/slice/test_connector_sign_in.py` -- one test per matrix row through `entry.main`, reusing `Microsoft` and `machine` -- the matrix is the contract
- [ ] `tests/connectors/test_graph_auth.py`, `test_graph_calendar_fetch.py`, `tests/slice/test_rotated_tokens_are_kept.py` -- one test per credential state (stale, interactive, declined, abandoned, unsaved rotation) and one for the 401-twice walk, each asserting the remedy contains `pm-ai connector sign-in <instance>`; a source scan asserting "Sign in again", "enrol again", "enrolling again" and "has no command" are absent from `pm_ai/connectors/graph/` -- the old wording cannot return

**Acceptance Criteria:**
- Given a Graph connector whose stored token Microsoft has retired, when `pm-ai connector sign-in graph:work` runs and the sign-in is approved, then `pm-ai connector check` reports it healthy and a fresh composition refreshes from the new token.
- Given the change, when `pm-ai connector check` reports any Graph credential state that needs a new sign-in, or a request Graph refused twice, then the remedy names `pm-ai connector sign-in <instance>`, and "Sign in again", "enrol again" and "has no command" appear nowhere under `pm_ai/connectors/graph/`.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Design Notes

The tenant comparison uses the `tid` claim of the sign-in's id token against the row's `tenant`. Who signed in — the `oid` claim, its `home_account_id` fallback, the blank-as-absent rule — is 8q's and is not restated here.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
- `grep -rn "Sign in again\|enrol again\|enrolling again\|has no command" pm_ai/connectors/graph/` -- expected: no output
