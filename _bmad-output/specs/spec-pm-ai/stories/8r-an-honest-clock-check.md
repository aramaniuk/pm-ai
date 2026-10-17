---
title: 'An honest clock check'
type: 'bugfix'
created: '2026-10-16'
status: 'done'
baseline_commit: '0d20d0fdfe4636f046323ec4004f43f5c88670ff'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Graph connector compares this machine's clock with the ID token's `iat` claim and warns past five minutes. On the field test every sign-in read "5:00.5 ahead" while the clock was within 0.15 s of a time server, so the Graph line is always `[warning]` and `pm-ai connector check` exits 4 on a healthy machine. `iat` is not a reading of Microsoft's clock at the answer: the claims reference describes it as when "the authentication for the token occurred".

**Approach:** Measure against the HTTP `Date` header of the token endpoint's answer — Microsoft's clock when it answered — paired with this machine's clock when that answer arrived, recorded by an HTTP client pm-ai hands to MSAL. `iat` is no longer read for skew; the tolerance, the warning and its remedy stay.

## Boundaries & Constraints

**Always:**
- **Skew is arrival minus (`Date` + 0.5 s)** — positive when this machine is ahead. `Date` has whole seconds, so its midpoint is the honest estimate; the warning still fires only when the absolute skew is strictly greater than `CLOCK_SKEW_TOLERANCE` (five minutes). Arrival is stamped with the auth adapter's injected clock (`now`), the one `_adopt` uses.
- **Only the token endpoint counts:** a `POST` whose URL path ends in `/oauth2/v2.0/token`. The device-code request (`/devicecode`), instance discovery and the OpenID configuration are never read for skew.
- **A reading belongs to its own acquisition.** The recording client writes into a slot held per thread; each acquisition clears its thread's slot before calling MSAL and takes the reading after, so a health probe and a harvest refreshing on one auth object at once (the I/O pool) never swap readings, and when one refresh calls MSAL twice (a rotated credential) the reading adopted is the one from the call whose token is adopted. MSAL makes its HTTP call on the calling thread.
- **Replaced on every adoption, never carried over.** A token MSAL served from its cache without a request, or an answer MSAL's throttle wrapper replayed from memory, carries no reading: skew is unmeasured (`None`). The health probe still forces a refresh, as it does today, so `connector check` and `doctor` always measure.
- **Unmeasured stays unmeasured:** no `Date`, an unparseable one, or one without a timezone is `None` — never zero, never a crash.
- **The recorder's mechanism.** The auth adapter owns the recorder as a field. The default client factory is bound to it when the adapter is built, so production — where wiring passes no factory (`wiring.py:1688-1690` passes one only when a test injects it) — always records. An injected two-argument factory keeps its signature; a test simulates an answer by writing a reading into `auth`'s recorder. A test asserts that a connector built by wiring with no injected factory uses the recording client.
- **The recording client's transport is MSAL's default, with the environment ignored:** a `requests` session with `trust_env=False` — no proxies, `REQUESTS_CA_BUNDLE` or `.netrc` taken from the environment, as the frontier and local-model clients do (AD-1) — certificate verification on, MSAL's default timeout (none), and one retry on a connection error through a mounted adapter (msal 1.38.0 `application.py:702-719`). MSAL still wraps it in its throttle handling.
- **`requests` is declared in the runtime extra** at the locked 2.34.2 (there today as MSAL's dependency), imported at call time like `msal`; the lock gains no package. Accepted as routine (2026-10-16).
- **The warning's purpose stands:** a clock more than five minutes off either way still turns the Graph line `[warning]`, with the same sentence and "Fix the clock" remedy.

**Ask First:**
- Nothing (answered 2026-10-17).

**Never:** No subtraction of a guessed five minutes from `iat`. No extra request to measure the clock. No change to the tolerance, the expiry arithmetic (relative `expires_in`), or what `8q` records about who signed in.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Healthy clock, backdated `iat` | `Date` matches arrival; `iat` five minutes earlier | skew under one second; Graph line `ok`; `connector check` exits 0 | N/A |
| Clock ahead | arrival 2 h 5 min after `Date` | `[warning]`, "2:05:00 ahead of the provider's" | N/A |
| Clock behind | arrival 10 min before `Date` | `[warning]`, "behind" | N/A |
| Just inside | arrival 5:00.4 after `Date` (4:59.9 after its midpoint) | `ok` | N/A |
| Just outside | arrival 5:01 after `Date` | `[warning]` | N/A |
| No `Date` header | answer without it | skew `None`; `ok` | N/A |
| Unparseable `Date` | `"yesterday"` | skew `None`; no crash | N/A |
| Cached token | MSAL answers from its cache | skew `None`, not the previous reading | N/A |
| Fixed clock | warning, then a refresh with matching `Date` | `ok` | N/A |
| Device-code sign-in | polling answers on the token endpoint, last one a token | skew from the answer that carried the token | N/A |
| Other endpoints | `/devicecode`, discovery, OpenID configuration | never read | N/A |
| Two acquisitions at once | probe and harvest refresh on two threads, `Date`s differing by an hour | each adopts its own reading | N/A |
| Rotated credential | one refresh calls MSAL twice | the reading of the call whose token is adopted | N/A |
| Environment ignored | the session handed to MSAL | its `trust_env` is False; verification on | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/graph/auth.py:166` `CLOCK_SKEW_TOLERANCE`; `:693` `_msal_public_client`; `:748` `GraphDeviceCodeAuth`, `:777` `client_factory`, `:778` `now`; `:949` `_refresh`, which can acquire twice; `:997` `check_health`, forced refresh at `:1061`, the warning at `:1161-1176`; `:1198` `_build_application`; `:1314` `_acquire`; `:1586` `_adopt`; `:1620-1641` `_measured_skew`, reading `iat`
- msal 1.38.0 `application.py:495-501` the `http_client` parameter ("Your implementation of abstract class HttpClient"); `:702-719` the default session (verify, proxies, timeout, one retry) and the throttle wrapper; `oauth2cli/http.py` the `post`/`get` and `Response.headers` contract
- `pm_ai/app/wiring.py:1553,1602,1688-1690` -- a factory passed only when injected
- `tests/connectors/test_graph_auth.py:727,750,775,791,804,827` -- the six skew tests, today driven by `iat`; eight test modules inject two-argument factories
- `pyproject.toml:67` beside `msal==1.38.0`; `uv.lock:1018-1019` `requests` 2.34.2

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/connectors/graph/auth.py` -- the per-thread recorder as a field; the MSAL HTTP client wrapping the stated session and recording token-endpoint answers; the default factory bound to the recorder at build; `_acquire` clears and takes its reading; `_measured_skew` reads it, never `iat` -- the fix
- [x] `pyproject.toml` -- declare `requests` in the runtime extra with a comment naming its one use; the spine's Stack row for it lands first (Spec Change Log)
- [x] `tests/connectors/test_graph_auth.py` -- the six skew tests driven by recorded readings; one test per matrix row, including two threads at once and a rotated credential -- proof
- [x] `tests/connectors/test_graph_auth.py` -- the recording client through a stub transport, no network: records a token-endpoint `POST`, ignores `/devicecode`, a `GET` and other paths; mounts one retry on connection errors; verification on -- the recorder's contract
- [x] `tests/slice/` (the connector-check slice test) -- a connector built by wiring with no injected factory uses the recording client
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the field-test clock entry resolved by `8r`; that entry exists on PR #71's branch, so this task lands after #71

**Acceptance Criteria:**
- Given a real sign-in on a machine whose clock is synchronised, when `pm-ai connector check` runs, then the Graph line is `ok` and the command exits 0.
- Given the change, when `grep -n '"iat"' pm_ai/connectors/graph/auth.py` runs, then nothing matches.
- Given the change, when `uv run pytest -q` runs, then all pass.

## Spec Change Log

- **2026-10-16, created.** Carries: skew from the token endpoint answer's HTTP `Date` against arrival, through an HTTP client handed to MSAL, replacing `iat` (five-minute backdating, undocumented, measured 5:00.5 on a synchronised clock); tolerance, warning and remedy unchanged; a cached answer unmeasured; `requests` declared directly (accepted as routine, 2026-10-16); two-argument factories kept.
- **2026-10-16, review:** the recorder mechanism made one thing (an adapter field the default factory is bound to; fakes write into it) and production's no-factory path tested; readings tied to their acquisition per thread, with rows for concurrent and repeated acquisitions; the token endpoint identified by method and path; arrival stamped with the injected clock; the whole-second `Date` compared at its midpoint with boundary rows; the session's verify, proxies, timeout and retry stated and the retry tested; the deferred-work task's dependency on PR #71 named; ACs no longer repeat matrix rows.
- **2026-10-16, review (consistency):** depends on spine amendment: Stack — a row for `requests` (2.34.2, imported directly by the Graph auth adapter for the recording HTTP client), made through `bmad-architecture` before this slice is built.
- **2026-10-16, spine gate:** frozen block edited (recorded here): an Ask First for the spec checkpoint — whether the `requests` session handed to MSAL sets `trust_env=False` or keeps MSAL's default (tech-currency F7); undecided, Andrei asked that it be raised here.
- **2026-10-17, Andrei's decision: `trust_env=False`.** The session handed to MSAL ignores the environment — no proxies, `REQUESTS_CA_BUNDLE` or `.netrc` — as the frontier and local-model clients do (AD-1); certificate verification stays on. Known cost: a user behind a corporate proxy cannot sign in. Frozen transport bullet edited, the Ask First closed, a matrix row added.

- **2026-10-17, built and reviewed.** Three reviewers (blind, edge case, verification gap); patched: the warning's drift rounds up to whole seconds so a warning never prints a drift equal to the tolerance (5:00.3 reads 0:05:01; 2:04:59.5 still reads 2:05:00); the default factory is resolved at `_build_application`, not stored in the field, so a copied adapter keeps its own recorder; the session is closed if MSAL refuses to build; tests added for a redemption that made no request (the `_call` clear was unprotected), for real msal 1.38.0 driven through the recording client over a stub transport, and for numeric-offset and `-0000` dates. Deferred, needs Andrei's decision: the session has no timeout (frozen here as MSAL's default). Rejected: proxy cost (decided), network latency counted as skew (sub-second against five minutes), the v2 path match (the authority is fixed).

## Design Notes

Three ways were weighed. **Subtracting five minutes from `iat`**: the ID token claims reference (checked 2026-10-16) defines `iat` as when authentication occurred and says nothing of backdating; the offset is an observation, not a contract, and a change to it would bring the false warning back silently. **A Graph resource answer's `Date`**: the health probe makes no Graph call, so it would cost a request. **The token endpoint's `Date`**: the answer already being made, from the service that issued the token, reachable through the HTTP-client hook MSAL documents. Chosen.

Midpoint, not floor: comparing arrival with `Date` itself would read a synchronised machine as up to a second ahead; against the five-minute tolerance it only matters at the boundary, which the two boundary rows pin.

The field test's own clock check used the same signal: two providers' `Date` headers agreed with a time server to the second (`field-test-2026-10-15.md`, "What ran").

## Verification

**Commands:**
- `uv run pytest tests/connectors/test_graph_auth.py -q` -- expected: every matrix row passes, no network
- `uv run pytest -q` -- expected: all pass
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Suggested Review Order

**Where the reading comes from**

- Entry point: skew is arrival minus the `Date` midpoint, never `iat`.
  [`auth.py:201`](../../../../pm_ai/connectors/graph/auth.py#L201)

- Records only a `POST` to the token endpoint; passes everything else through.
  [`auth.py:279`](../../../../pm_ai/connectors/graph/auth.py#L279)

- One slot per thread, so concurrent acquisitions never swap readings.
  [`auth.py:242`](../../../../pm_ai/connectors/graph/auth.py#L242)

- The session handed to MSAL: `trust_env=False`, verify on, one retry.
  [`auth.py:307`](../../../../pm_ai/connectors/graph/auth.py#L307)

**How an acquisition owns its reading**

- Default factory resolved at build, bound to this adapter's recorder.
  [`auth.py:1399`](../../../../pm_ai/connectors/graph/auth.py#L1399)

- Each MSAL call clears its thread's slot first; a cached answer reads `None`.
  [`auth.py:1501`](../../../../pm_ai/connectors/graph/auth.py#L1501)

- The adopted token takes the reading of the call that produced it.
  [`auth.py:1833`](../../../../pm_ai/connectors/graph/auth.py#L1833)

- Warning text unchanged except drift rounds up to whole seconds.
  [`auth.py:1367`](../../../../pm_ai/connectors/graph/auth.py#L1367)

**Peripherals**

- One test per matrix row, starting at the backdated-`iat` healthy case.
  [`test_graph_auth.py:799`](../../../../tests/connectors/test_graph_auth.py#L799)

- Real msal 1.38.0 through the recording client, no network.
  [`test_graph_auth.py:1939`](../../../../tests/connectors/test_graph_auth.py#L1939)

- Wiring with no injected factory uses the recording client.
  [`test_connector_add_graph.py:1061`](../../../../tests/slice/test_connector_add_graph.py#L1061)

- `requests` declared in the runtime extra at the locked version.
  [`pyproject.toml:73`](../../../../pyproject.toml#L73)
