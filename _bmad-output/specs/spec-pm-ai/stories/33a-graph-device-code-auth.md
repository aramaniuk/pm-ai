---
title: 'Graph device-code auth'
type: 'feature'
created: '2026-09-02'
status: 'ready-for-dev'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Graph transcript adapter reads from a `_fake_api` dict (`connectors/transcripts/graph.py:14`) and nothing in the repository can obtain a Microsoft token. Reaching Teams at all needs delegated auth, and the flow that fits a local-first single-PM tool is device code: the PM signs in once interactively at a URL, no client secret sits on the laptop, and no redirect URI has to be hosted.

**It does not avoid tenant-admin consent, and the first draft claimed it did.** What the delegated flow avoids is the *app-only* path and the application access policy that path requires for transcripts. Consent is a separate question, and slice 0 answered it the other way: `ChannelMessage.Read.All` and `OnlineMeetingTranscript.Read.All` normally require an administrator, the grant slice 0 obtained came from one, and whether an ordinary PM can self-consent to the remaining five is unmeasured. This does not change which flow fits — admin consent is a one-time app-registration prerequisite, not a per-sign-in step — but it does change what the operator has to arrange before a first enrolment can succeed at all, and the Always clause below is the authority on that.

**Approach:** Add `GraphAuthPort` to `pm_ai/ports/` and the MSAL device-code adapter at `pm_ai/connectors/graph/auth.py`, storing the refresh token through `8b`'s enrolment. No resource is fetched in this story.

## Boundaries & Constraints

**Always:**
- **The adapter lives in `connectors`, not `platform`.** The `http-confined-to-adapters` contract (`.importlinter:49-60`) forbids HTTP clients in `pm_ai.platform` and states the intent as "only inbound connectors and outbound skills may speak HTTP at all". Device-code flow talks to the Microsoft token endpoint, so it is HTTP. Token **custody** is `platform` — the Keychain behind `KeychainPort`, from 1d. Token **acquisition** is `connectors`.
- **The scope set is declared in one place**: **seven** resource permissions — `Calendars.Read`, `Chat.Read`, `ChannelMessage.Read.All`, `OnlineMeetingTranscript.Read.All`, `OnlineMeetings.Read`, `Team.ReadBasic.All`, `Channel.ReadBasic.All` — plus `offline_access`, which is not a resource permission and is what makes silent refresh possible at all. All seven are delegated, and none needs protected-API approval or an application access policy.
  **The set covers the whole connector, not this wave.** `Chat.Read`, `ChannelMessage.Read.All` and the two enumeration scopes are all `33d`'s, and `OnlineMeetingTranscript.Read.All` and `OnlineMeetings.Read` are `33e`'s — none is called in wave 1. They are declared here anyway because consent happens once, at enrolment, and the partial-consent rule below turns a scope added later into a forced re-enrolment rather than a degradation. One consent, asked for once, is the point of declaring them in one place.
  **Declaring more raises the bar for a successful enrolment, and that is the accepted cost.** The granted set is compared against the declared set on every acquisition, so an administrator who grants six of seven produces a connector that refuses entirely rather than one that works for calendars. That is the intended behaviour — `8a` exists to stop a connector reporting coverage it does not have — but it means the app registration must carry all seven before anyone can enrol at all.
  **`OnlineMeetings.Read` is not optional, and was missing until slice 0 ran.** Reading a transcript and *finding the meeting that holds it* are two separate grants: `GET /me/onlineMeetings?$filter=JoinWebUrl eq '...'` is how `33e` resolves a calendar event to the meeting whose transcripts it wants, and without this permission that call returns `403 Forbidden "Insufficient permissions"` and the transcript endpoint is never reached. That 403 is **indistinguishable by status code** from the tenant-level transcript switch, so a connector missing this scope reports "this tenant has disabled transcripts" when the truth is "we never asked for the right permission".
  **`Team.ReadBasic.All` and `Channel.ReadBasic.All` are the same split again, for messages.** `ChannelMessage.Read.All` reads a channel message; it does not permit enumerating the teams and channels needed to find one. Slice 0 measured both refusals. Without them `33d` cannot walk to a message at all, and its alternative — explicit team and channel ids in configuration — is foreclosed by this decision rather than left open.
  **Admin consent is stated per permission, not as a blanket.** `ChannelMessage.Read.All` and `OnlineMeetingTranscript.Read.All` are the two that normally require an administrator. Slice 0 ran with an administrator granting the scopes it requested, and every scope it requested consented and returned a refresh token; it did **not** establish that an ordinary user can self-consent, so a deployment where the PM is not an admin is still an unknown. The count is deliberately not restated in this clause — the declared set is above, and a number repeated in prose is a number that goes stale, as both `three of four` and `all six` did between 2026-09-02 and 2026-09-06.
  **The tenant-level transcript switch is real and stays handled.** Slice 0 established that the permitted path exists and works end to end, which is what `33e`'s scope depended on — not that every tenant permits it. `GraphAccessToTranscriptsDisabled` remains a switch no request can work around, and `33e` degrades rather than retries.
- **The refresh token is a credential** and goes through `8b`: sealed store first, connector configuration second. It never appears in output, logs or tracebacks.
- **A stale credential reports as stale.** An expired refresh token is a distinct health state from a provider being unreachable, because the remedies differ — one needs the PM to sign in again, the other needs waiting.
- **The human does the signing in.** The adapter prints a code and a URL; it opens no browser and handles no password.

- **`msal` joins the `http-confined-to-adapters` contract in this slice.** As written the contract names `httpx`, `requests` and `aiohttp` (`.importlinter:47-60`), and MSAL reaches `requests` transitively — so the misplacement this slice's first clause argues against would have passed the gate. Deciding it here rather than deferring is the point: a violation that passes the gate is worse than one that fails.

**Ask First:** Nothing. Both questions this slice raised are decided above — the `msal` contract in the first draft, and the enumeration scopes on 2026-09-06.

**Never:** No calendar, message or transcript fetching — `33b`, `33d`, `33e`. No app-only or client-credentials path: a second auth mode with nothing exercising it is untested code. No token in `connectors/graph.json`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First sign-in | no stored token | code and URL presented; on completion the refresh token is sealed | N/A |
| Silent refresh | valid refresh token | access token returned without prompting | N/A |
| Refresh token expired | stored token rejected | reported as stale, naming re-enrolment | `CredentialStale` |
| PM abandons sign-in | device code expires unused | refused; nothing stored | `AuthTimedOut` |
| Consent declined | user declines a scope | refused, naming the declined scope | `AuthDeclined` |
| Partial consent | some scopes granted | refused rather than degraded — a connector holding six of seven scopes fails at an unpredictable resource | `AuthDeclined` |
| Network unreachable at token endpoint | no route | reported as unreachable, distinctly from stale | `GraphUnreachable` |
| Health probe, valid token | credential good | healthy within CAP-35's 10s bound | probe reports, never raises |
| Probe with nothing enrolled | no credential ever stored | reports `ABSENT`, distinctly from `FAILING` — a fresh install is not a broken machine | probe reports, never raises |
| Token expires mid-harvest | page 3 returns 401 on a valid refresh token | silent refresh, the page retried once; pages already walked retained | `CredentialStale` only after the retry |
| Polling throttled | token endpoint answers `slow_down` or 429 | the interval from the device-code response is honoured and backed off | retried, not counted as abandoned |
| Conditional access | AAD returns `interaction_required` | reported distinctly — the remedy is an interactive sign-in, neither waiting nor re-enrolment | `InteractionRequired` |
| Local clock skewed | laptop clock hours off | expiry judged with tolerance; skew reported as its own state | distinct from stale |
| MSAL cache ambiguous | zero or several cached accounts | the enrolled account is identified explicitly; ambiguity refused | `CredentialStale` |
| Concurrent refresh | two processes refresh, AAD rotates the token | the sealed token updated under a lock; the loser retries before reporting stale | retried |
| Scopes narrowed after enrolment | an admin revokes one permission | the granted set is compared against the declared set on **every** acquisition, not only at enrolment | `AuthDeclined` |
| A declared scope was never granted | the app registration omits `OnlineMeetings.Read` | refused at acquisition by the same set comparison, naming the missing scope — **not** deferred to the 403 it would cause later, which reads as a tenant restriction | `AuthDeclined` |

</frozen-after-approval>

## Code Map

- `pm_ai/ports/__init__.py` -- add `GraphAuthPort` beside the existing ports
- `pm_ai/connectors/graph/auth.py` -- new; the MSAL adapter and the scope declaration
- `pyproject.toml` -- pin `msal`, following the pinning discipline the `anthropic` entry documents
- `.importlinter:49-60` -- the contract that decides this module's home
- `pm_ai/core/connector_enrolment.py` -- `8b`, which seals the token
- `pm_ai/core/connector_enrolment.py:142` -- `stored_credentials`, the reader `8b` built; nothing outside enrolment's own duplicate check calls it
- `pm_ai/app/wiring.py:256-319` -- `_enrolled_connectors`, which skips `system != "gitlab"` with the comment "33a adds Graph" and constructs every adapter holding no credential
- `pm_ai/connectors/gitlab.py:56-60` -- the `credential: str | None` field, whose comment says `8b` owns putting a real one there; `8b` is done and did not
- `pm_ai/platform/keychain.py` -- custody, the half that stays in `platform`
- `pm_ai/connectors/transcripts/graph.py:14` -- the `_fake_api` that `33e` replaces using this auth
- `_bmad-output/implementation-artifacts/slice-0-graph-spike-2026-09-06.md` -- what a live tenant actually answered; the source of three of the seven scopes (`OnlineMeetings.Read`, `Team.ReadBasic.All`, `Channel.ReadBasic.All`), of the Ask First now answered above, and of the admin-consent correction in the Intent

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/ports/__init__.py` -- add `GraphAuthPort`
- [ ] `pm_ai/connectors/graph/auth.py` -- add the device-code adapter, the **seven**-permission scope constant plus `offline_access`, and the **five** error types the matrix names: `CredentialStale`, `AuthTimedOut`, `AuthDeclined`, `GraphUnreachable`, `InteractionRequired`
- [ ] `pyproject.toml` -- pin `msal`
- [ ] `.importlinter` -- add `msal` to `http-confined-to-adapters`'s forbidden modules
- [ ] `pm_ai/app/wiring.py` -- read the sealed credential back in `_enrolled_connectors` and hand it to the adapter it builds. `deferred-work.md` assigns this here ("it is the wiring 33a needs"), and this is the slice that makes it observable: until a transport exists an adapter needs no token, but `pm-ai connector check` already reports a just-enrolled connector as `ABSENT`. `app` may import both `core` and `storage`, so `stored_credentials(storage)` is reachable from here and from nowhere lower
- [ ] `tests/connectors/test_graph_auth.py` -- the matrix against a fake MSAL client; no network in any test

**Acceptance Criteria:**
- Given a stored refresh token that the provider rejects, when a token is requested, then `CredentialStale` is raised and the health probe reports stale rather than unreachable — the two states an operator must be able to tell apart.
- Given `pm_ai/platform/graph_auth.py` is created temporarily importing `msal`, then `lint-imports` **fails**; and with the adapter at `pm_ai/connectors/graph/auth.py` it passes. Verified by deliberate misplacement, because both original criteria were true either way: the contract did not name `msal`, and `pm_ai.platform` not importing `pm_ai.connectors` is already guaranteed by the sibling row of the layering contract with zero new code.
- Given the declared scope set, then it equals exactly the seven resource permissions plus `offline_access`, asserted as a set so a silent addition *or omission* fails. The omission direction is the one slice 0 exercised: the set was short by `OnlineMeetings.Read`, and nothing failed until a live tenant returned a 403 that read as a tenant restriction.
- Given an app registration that grants every declared scope except `OnlineMeetings.Read`, when a token is acquired, then `AuthDeclined` names that scope — rather than the connector starting and `33e` reporting the tenant as having transcripts disabled.
- Given a stored token the provider rejects for conditional access rather than expiry, then `InteractionRequired` is raised, not `CredentialStale` — three remedies, three states.
- Given a connector enrolled in a previous process, when the daemon composes, then the adapter it builds holds the credential from the sealed store and `pm-ai connector check` no longer reports it `ABSENT` — the state `8b`'s own success message ("active at the next start") already promised away.

The credential-not-in-`connectors/graph.json` criterion moved to `8b`, which owns that write; this slice's tests are fake-MSAL unit tests with no storage in the path, so it could never have been asserted here.

## Spec Change Log

- **2026-09-06, renegotiated on instruction: the Intent's consent premise was false, and two scope counts had gone stale.** Three corrections, one cause — the frozen block was written before a live tenant answered, and then amended twice without its prose counts following.
  **"No tenant-admin consent" was the rationale for choosing device code, and it is not true.** The delegated flow avoids the *app-only* path and the application access policy that path requires for transcripts; it does not avoid consent. Slice 0's grant came from an administrator, and two of the declared permissions normally require one. The flow choice survives — admin consent is a one-time app-registration prerequisite, not a per-sign-in step — but an operator reading the Intent would have planned a first enrolment that could not have succeeded. The Always clause, which had already been corrected per permission, is now named as the authority.
  **Two counts restated in prose had drifted.** The partial-consent matrix row still said "three of four scopes" and the admin-consent clause still said "all six consent" — both true when written, neither true after the set went to five and then seven. The row now says six of seven; the clause no longer restates a count at all, and says why. The declared set itself is stated once, in one place, which is the property those two sentences were quietly undermining.
  **The `no application access policy` half of the original claim is kept**, unchanged and still correct: it was verified against the Graph reference on 2026-09-01 and re-confirmed by slice 0's end-to-end transcript read on the delegated path.

- **2026-09-06, three unfrozen corrections the amendments left behind.**
  **The task list said "the four error types" while the matrix named five.** `InteractionRequired` entered the matrix in the 2026-09-02 review as a third distinct remedy and the task's count was never updated, so an implementer working from the task list would have built four and left conditional access collapsed into staleness — the exact defect that review opened. All five are now named rather than counted.
  **The Code Map credited slice 0 with "the fifth scope"**; it is the source of three of the seven, and of the admin-consent correction above.
  **The credential read-back joins this slice's Code Map and tasks**, where `deferred-work.md` had already assigned it ("it is the wiring 33a needs") without the spec recording it. `8b` built `stored_credentials` and nothing outside enrolment's duplicate check calls it, `_enrolled_connectors` constructs every adapter with `credential=None`, and `gitlab.py`'s field comment still says `8b` owns putting a real one there — `8b` is done and did not. The consequence is live now, not at `33b`: `pm-ai connector check` reports a just-enrolled connector `ABSENT`, contradicting `8b`'s own "active at the next start". Reading it in `_enrolled_connectors` is compatible with the frozen **Never** — a sealed-store read is not a resource fetch.

- **2026-09-06, the enumeration Ask First answered: declare them now.** `Team.ReadBasic.All` and `Channel.ReadBasic.All` join the declared set, taking it to seven resource permissions plus `offline_access`. The reasoning is the partial-consent rule: a scope added after a PM has enrolled stops the connector until they enrol again, so the cheap moment to ask is the only one. `ChannelMessage.Read.All` was already declared, so the incremental ask — enumerating teams and channels the PM is already a member of — is strictly smaller than the consent already being sought.
  **The cost is recorded rather than glossed.** Declaring seven means an administrator must grant all seven before anyone can enrol; six of seven produces a connector that refuses entirely rather than one that works for calendars. That follows from the partial-consent rule this slice already had, and is the intended behaviour under `8a`, but it is a higher bar than five and the spec now says so.
  **`33d`'s alternative is foreclosed by this**, deliberately: taking explicit team and channel ids in configuration is no longer the fallback, because the scopes to enumerate will be present.

- **2026-09-06, amended against slice 0, on the human's instruction.** The frozen block was renegotiated because a live tenant contradicted it.
  **The declared scope set was short by one, and the omission was undetectable from the repository.** `OnlineMeetings.Read` is required to resolve a join URL to the meeting whose transcripts `33e` wants. Without it that lookup returns `403 Forbidden "Insufficient permissions"` — the same status as the tenant-level transcript switch — so the connector would have reported "this tenant has disabled transcripts" when the truth was "we never asked for the right permission". Four resource permissions became five, the set assertion now fails on omission as well as addition, and a matrix row and an acceptance criterion pin the misdiagnosis specifically.
  **The blanket "slice 0 exists to find out" language is replaced by what it found.** The permitted transcript path exists and works end to end on the delegated flow, so `33e`'s scope is not forced into degradation-only. All scopes consented and a refresh token was returned — with an administrator granting them, which does *not* establish that a non-admin PM can self-consent. That remains unknown and is stated as unknown.
  **A second reading-versus-finding split is now an Ask First.** `ChannelMessage.Read.All` does not permit enumerating teams and channels, so `33d` needs two more scopes or explicit ids in configuration. It is decided here rather than there because this slice's partial-consent rule turns a late scope addition into a forced re-enrolment rather than a degradation.
  **No task, verification command or error type changed** beyond the scope constant's size. The adapter, the port, the `msal` contract decision and the matrix's auth states are untouched.

- **2026-09-02, multi-lens review.** Every architecture criterion in the first draft was unfalsifiable.
  **"`lint-imports` holds" was true either way.** `http-confined-to-adapters` names `httpx`, `requests` and `aiohttp` — not `msal`, which MSAL reaches transitively — so the misplacement this spec spends a paragraph arguing against would have passed the gate it cited as proof. And "`pm_ai.platform` imports nothing from `pm_ai.connectors.graph`" is already guaranteed by the layer contract without any new code. The `msal` question is therefore decided here rather than deferred to Ask First, and the criterion is now verified by deliberate temporary misplacement.
  **Five scopes were listed and called "four" twice**, with the criterion asserting a match against "the four delegated permissions named above" — so the set assertion, the whole insurance against silent consent widening, could not be written from the spec. Four resource permissions plus `offline_access`, now stated as such. The blanket admin-consent claim also covered the two permissions slice 0 exists to probe, and is now per-permission.
  **One criterion belonged to another slice.** The `connectors/graph.json` check could never run here: this slice's tests are fake-MSAL unit tests with no storage in the path, and `8b` performs that write. Moved.
  The edge-case lens added the auth states a real tenant produces: a token expiring mid-harvest (which would have failed a long harvest as stale on a valid credential), `slow_down` while polling, `interaction_required` from conditional access as a **third** distinct remedy the two-state design collapsed, an ambiguous MSAL account cache, concurrent refresh against AAD's token rotation, and scopes narrowed by an admin after enrolment — the last meaning the partial-consent refusal was enforced only at enrolment.
## Design Notes

Refusing partial consent rather than degrading is the one choice here that costs capability. The alternative — run with whatever scopes were granted and fail per-resource — produces a connector whose health depends on which endpoint you happen to hit, and `8a` exists precisely to stop connectors reporting coverage they do not have. A connector that will not start is diagnosable in one line.

Asserting the scope set as a set, in a test, is cheap insurance against the ordinary drift where someone adds `Mail.Read` for a feature and the consent prompt silently widens. Every scope in this list is a thing the PM is agreeing to hand over.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_auth.py -q` -- expected: all matrix rows pass, no network
- `uv run lint-imports` -- expected: contracts kept
- `uv run pytest -q` -- expected: no new failures
