---
title: 'Graph device-code auth'
type: 'feature'
created: '2026-09-02'
status: 'in-review'
review_loop_iteration: 0
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Graph transcript adapter reads from a `_fake_api` dict (`connectors/transcripts/graph.py:14`) and nothing in the repository can obtain a Microsoft token. Reaching Teams at all needs delegated auth, and the flow that fits a local-first single-PM tool is device code: the PM signs in once interactively, no tenant-admin consent, no application access policy.

**Approach:** Add `GraphAuthPort` to `pm_ai/ports/` and the MSAL device-code adapter at `pm_ai/connectors/graph/auth.py`, storing the refresh token through `8b`'s enrolment. No resource is fetched in this story.

## Boundaries & Constraints

**Always:**
- **The adapter lives in `connectors`, not `platform`.** The `http-confined-to-adapters` contract (`.importlinter:49-60`) forbids HTTP clients in `pm_ai.platform` and states the intent as "only inbound connectors and outbound skills may speak HTTP at all". Device-code flow talks to the Microsoft token endpoint, so it is HTTP. Token **custody** is `platform` — the Keychain behind `KeychainPort`, from 1d. Token **acquisition** is `connectors`.
- **The scope set is declared in one place** and is exactly what wave 1 and 2 need: `Calendars.Read`, `Chat.Read`, `ChannelMessage.Read.All`, `OnlineMeetingTranscript.Read.All`, `offline_access`. All four are delegated permissions that need no admin consent and no protected-API approval, verified against the Graph reference 2026-09-01.
- **The refresh token is a credential** and goes through `8b`: sealed store first, connector configuration second. It never appears in output, logs or tracebacks.
- **A stale credential reports as stale.** An expired refresh token is a distinct health state from a provider being unreachable, because the remedies differ — one needs the PM to sign in again, the other needs waiting.
- **The human does the signing in.** The adapter prints a code and a URL; it opens no browser and handles no password.

**Ask First:** Whether `.importlinter` should name `msal` in the HTTP-client contract. MSAL reaches `requests` transitively, so the contract as written would not have caught the misplacement described above — a violation that passes the gate is worse than one that fails it, and widening the contract is a decision about the gate itself.

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
- [ ] `tests/connectors/test_graph_auth.py` -- the matrix against a fake MSAL client; no network in any test

**Acceptance Criteria:**
- Given a stored refresh token that the provider rejects, when a token is requested, then `CredentialStale` is raised and the health probe reports stale rather than unreachable — the two states an operator must be able to tell apart.
- Given a completed sign-in, when the connector configuration at `connectors/graph.json` is read, then it contains no token material.
- Given `lint-imports` runs, then `pm_ai.platform` imports nothing from `pm_ai.connectors.graph` and the HTTP contract holds.
- Given the declared scope set, then it matches the four delegated permissions named above, asserted as a set so a silent addition fails.

## Design Notes

Refusing partial consent rather than degrading is the one choice here that costs capability. The alternative — run with whatever scopes were granted and fail per-resource — produces a connector whose health depends on which endpoint you happen to hit, and `8a` exists precisely to stop connectors reporting coverage they do not have. A connector that will not start is diagnosable in one line.

Asserting the scope set as a set, in a test, is cheap insurance against the ordinary drift where someone adds `Mail.Read` for a feature and the consent prompt silently widens. Every scope in this list is a thing the PM is agreeing to hand over.

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_auth.py -q` -- expected: all matrix rows pass, no network
- `uv run lint-imports` -- expected: contracts kept
- `uv run pytest -q` -- expected: no new failures
