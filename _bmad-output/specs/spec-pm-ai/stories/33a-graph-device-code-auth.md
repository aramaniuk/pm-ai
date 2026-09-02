---
title: 'Graph device-code auth'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 1
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Graph transcript adapter reads from a `_fake_api` dict (`connectors/transcripts/graph.py:14`) and nothing in the repository can obtain a Microsoft token. Reaching Teams at all needs delegated auth, and the flow that fits a local-first single-PM tool is device code: the PM signs in once interactively, no tenant-admin consent, no application access policy.

**Approach:** Add `GraphAuthPort` to `pm_ai/ports/` and the MSAL device-code adapter at `pm_ai/connectors/graph/auth.py`, storing the refresh token through `8b`'s enrolment. No resource is fetched in this story.

## Boundaries & Constraints

**Always:**
- **The adapter lives in `connectors`, not `platform`.** The `http-confined-to-adapters` contract (`.importlinter:49-60`) forbids HTTP clients in `pm_ai.platform` and states the intent as "only inbound connectors and outbound skills may speak HTTP at all". Device-code flow talks to the Microsoft token endpoint, so it is HTTP. Token **custody** is `platform` — the Keychain behind `KeychainPort`, from 1d. Token **acquisition** is `connectors`.
- **The scope set is declared in one place**: **four** resource permissions — `Calendars.Read`, `Chat.Read`, `ChannelMessage.Read.All`, `OnlineMeetingTranscript.Read.All` — plus `offline_access`, which is not a resource permission and is what makes silent refresh possible at all. All four resource permissions are delegated, and verified against the Graph reference on 2026-09-01 as needing no protected-API approval and no application access policy. **Admin consent is stated per permission, not as a blanket:** whether this tenant's administrator has restricted user consent for `ChannelMessage.Read.All` and `OnlineMeetingTranscript.Read.All` is exactly what slice 0's spike exists to find out, and the design records a tenant-level transcript switch that no request can work around.
- **The refresh token is a credential** and goes through `8b`: sealed store first, connector configuration second. It never appears in output, logs or tracebacks.
- **A stale credential reports as stale.** An expired refresh token is a distinct health state from a provider being unreachable, because the remedies differ — one needs the PM to sign in again, the other needs waiting.
- **The human does the signing in.** The adapter prints a code and a URL; it opens no browser and handles no password.

- **`msal` joins the `http-confined-to-adapters` contract in this slice.** As written the contract names `httpx`, `requests` and `aiohttp` (`.importlinter:47-60`), and MSAL reaches `requests` transitively — so the misplacement this slice's first clause argues against would have passed the gate. Deciding it here rather than deferring is the point: a violation that passes the gate is worse than one that fails.

**Ask First:** Nothing. The `msal` contract question, deferred in the first draft, is decided above.

**Never:** No calendar, message or transcript fetching — `33b`, `33c`, `33d`. No app-only or client-credentials path: a second auth mode with nothing exercising it is untested code. No token in `connectors/graph.json`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First sign-in | no stored token | code and URL presented; on completion the refresh token is sealed | N/A |
| Silent refresh | valid refresh token | access token returned without prompting | N/A |
| Refresh token expired | stored token rejected | reported as stale, naming re-enrolment | `CredentialStale` |
| PM abandons sign-in | device code expires unused | refused; nothing stored | `AuthTimedOut` |
| Consent declined | user declines a scope | refused, naming the declined scope | `AuthDeclined` |
| Partial consent | some scopes granted | refused rather than degraded — a connector holding three of four scopes fails at an unpredictable resource | `AuthDeclined` |
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

</frozen-after-approval>

## Code Map

- `pm_ai/ports/__init__.py` -- add `GraphAuthPort` beside the existing ports
- `pm_ai/connectors/graph/auth.py` -- new; the MSAL adapter and the scope declaration
- `pyproject.toml` -- pin `msal`, following the pinning discipline the `anthropic` entry documents
- `.importlinter:49-60` -- the contract that decides this module's home
- `pm_ai/core/connector_enrolment.py` -- `8b`, which seals the token
- `pm_ai/platform/keychain.py` -- custody, the half that stays in `platform`
- `pm_ai/connectors/transcripts/graph.py:14` -- the `_fake_api` that `33d` replaces using this auth

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/ports/__init__.py` -- add `GraphAuthPort`
- [ ] `pm_ai/connectors/graph/auth.py` -- add the device-code adapter, the scope constant, and the four error types
- [ ] `pyproject.toml` -- pin `msal`
- [ ] `.importlinter` -- add `msal` to `http-confined-to-adapters`'s forbidden modules
- [ ] `tests/connectors/test_graph_auth.py` -- the matrix against a fake MSAL client; no network in any test

**Acceptance Criteria:**
- Given a stored refresh token that the provider rejects, when a token is requested, then `CredentialStale` is raised and the health probe reports stale rather than unreachable — the two states an operator must be able to tell apart.
- Given `pm_ai/platform/graph_auth.py` is created temporarily importing `msal`, then `lint-imports` **fails**; and with the adapter at `pm_ai/connectors/graph/auth.py` it passes. Verified by deliberate misplacement, because both original criteria were true either way: the contract did not name `msal`, and `pm_ai.platform` not importing `pm_ai.connectors` is already guaranteed by the sibling row of the layering contract with zero new code.
- Given the declared scope set, then it equals exactly the four resource permissions plus `offline_access`, asserted as a set so a silent addition fails.
- Given a stored token the provider rejects for conditional access rather than expiry, then `InteractionRequired` is raised, not `CredentialStale` — three remedies, three states.

The credential-not-in-`connectors/graph.json` criterion moved to `8b`, which owns that write; this slice's tests are fake-MSAL unit tests with no storage in the path, so it could never have been asserted here.

## Spec Change Log

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
