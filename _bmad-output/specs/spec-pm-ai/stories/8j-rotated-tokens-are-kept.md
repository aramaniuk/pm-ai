---
title: 'A refreshed sign-in survives a restart, and no other credential is touched'
type: 'bugfix'
created: '2026-10-08'
status: 'done'
review_loop_iteration: 0
baseline_commit: '65c4511cf3211b5ea4879a823179982b0e0a268e'
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** pm-ai can tell you to sign in to Microsoft again when nothing was wrong.

When pm-ai talks to Microsoft Graph, Microsoft may hand back a new long-lived sign-in token and retire the old one. pm-ai keeps the new token only in memory. Every `pm-ai` command is its own short run, so the new token is lost when the command ends, and the next run starts from the old one stored at enrolment. Once Microsoft has retired that one, the connector reports its credential as stale and tells you to sign in again — for a sign-in that was fine.

There is a second, quieter hazard in the same place. The piece of pm-ai that would save the new token treats the encrypted credentials file as a single value with its own lock. The file actually holds every connector's credential, keyed by name, and enrolment edits it under a different lock. Saving a token that way would erase every other connector's credential, or race an enrolment and lose one of the two writes.

**Approach:** Save the refreshed token back into the encrypted credentials file, for that one connector only, under the same lock enrolment uses, and read it back from the file rather than from a copy taken when pm-ai started.

## Boundaries & Constraints

**Always:**
- **A rotated token is saved before it is used**, as it is today, so a token Microsoft has already retired is never the only one on disk.
- **Saving one connector's token changes nothing else in the file**: other connectors' credentials and any other top-level keys stay byte-for-byte as they were in meaning.
- **One lock for the credentials file.** Enrolment and token saving take the same lock, so neither can lose the other's write.
- **The token is read from the file each time it is needed**, not from a copy taken at start-up, so a run sees a token another run saved.
- **A busy credentials file is waited for briefly, then reported as busy** — never as a fault in pm-ai. If a rotated token cannot be saved, the message says the sign-in must be redone and why.
- **The connector never chooses where a token is stored** (AD-39). It hands the new token to what it was given; the composition root decides where it goes.
- **Secrets never leave the encrypted file**: not into logs, diagnostics or error messages.

**Ask First:** Any change to the credentials file's layout. Any change to how long a busy file is waited for beyond a couple of seconds.

**Never:** No change to enrolment's refusal of a duplicate connector name. No new command. No scheduler or background refresh. No change to the device-code sign-in flow.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Rotation, then a new run | Microsoft issues a new token in run 1 | run 2 uses the new token; no re-sign-in | N/A |
| No rotation | Microsoft returns the same token | file not rewritten | N/A |
| Other connectors present | GitLab and Graph both enrolled; Graph rotates | GitLab's credential and every other key unchanged | N/A |
| Two runs at once | run A rotates while run B reads | B either reads the old token and retries with A's, or reads A's | existing retry on a changed store |
| Credentials file busy | another run holds the lock | wait briefly, then succeed or report "busy" | named busy failure, not "a bug in pm-ai" |
| Rotated token cannot be saved | write fails after Microsoft rotated | error says the sign-in must be redone and why | existing message kept |
| Connector removed from the file between read and save | its entry is gone | refused, naming the connector | nothing written |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/graph/auth.py:235-266` -- `RefreshTokenStore` protocol: `read() -> str | None`, `write(credential)`, `exclusive()` (not re-entrant; taken once for read, once for write-back, never across the network call)
- `pm_ai/connectors/graph/auth.py:268-329` -- `SealedCredential` (`{"home_account_id", "refresh_token"}` as a JSON string); its docstring frames the store as "exactly one credential" — correct per instance, misleading about the file
- `pm_ai/connectors/graph/auth.py:333-364` -- `InMemoryRefreshTokenStore`, the only implementation; `exclusive()` is a `nullcontext`
- `pm_ai/connectors/graph/auth.py:635-689`, `:671-683`, `:1033-1064` -- `access_token`, the retry when the store changed underneath, `_rotate` (writes only when the issued token differs; raises `GraphAuthError` when the write fails)
- `pm_ai/connectors/graph/auth.py:691-835`, `:810` -- `check_health`; an `ArtifactBusy` would fall through to the catch-all that reports "a bug in pm-ai"
- `pm_ai/app/wiring.py:863-881` -- `_graph_connector` builds `InMemoryRefreshTokenStore(credential=credential)`; comment at 873-878 admits the limit. The replacement is built here
- `pm_ai/app/wiring.py:601-720`, `:919`, `:945` -- `_enrolled_connectors`, `_credential_for`, `_stored_credentials` (swallows errors → `{}`; unchanged here)
- `pm_ai/core/connector_enrolment.py:98`, `:163-300`, `:405-433` -- `CREDENTIALS_KEY = "connectors"`; `enrol_connector` reads, merges and writes under `storage.exclusive(scope=APPLICATION, artifact="config.json")`; `_credentials_in`. File shape: `{"connectors": {"<instance>": {"system", "credential"}}, …}`. A keyed update function for one instance's `credential` belongs here, reusing the same lock and merge, without enrolment's duplicate refusal
- `pm_ai/core/connector_enrolment.py:95-97` -- comment guessing the Graph token would be a top-level key; wrong, correct it
- `pm_ai/storage/service.py:1290+` -- `StorageService.exclusive`: `.config.json.claim` via `O_CREAT|O_EXCL`, refuses at once with `ArtifactBusy` (`ports/__init__.py:159`)
- `tests/connectors/test_graph_auth.py:88`, `:864`, `:491`, `:923-992` -- `FakeMsal`, `RotatingStore`, rotation tests
- `tests/core/test_connector_enrolment.py:153`, `:166`, `:485-671` -- key preservation and composition tests to extend

## Tasks & Acceptance

**Execution:**
- [x] `pm_ai/core/connector_enrolment.py` -- add a keyed update of one instance's credential under the existing lock; refuse a missing instance or a system mismatch; correct the stale comment -- the one writer for the file
- [x] `pm_ai/app/wiring.py` -- a `RefreshTokenStore` for one Graph instance: reads the file live, writes through the keyed update, `exclusive()` is the storage claim with a short bounded wait; `_graph_connector` uses it -- the custody the daemon owns
- [x] `pm_ai/connectors/graph/auth.py` -- report a busy credentials file as busy rather than "a bug in pm-ai"; correct `SealedCredential`'s docstring about the file -- honest messages
- [x] `tests/` -- one test per matrix row through the real `StorageService`, including a rotation in one composition seen by a fresh composition -- the matrix is the contract
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the rotation entry resolved; record that AD-39's "a connector never persists, refreshes" line contradicts its revised custody bullet and needs the architecture skill

**Acceptance Criteria:**
- Given a Graph connector whose token Microsoft rotated in one run, when a second run composes, then it uses the rotated token and reports the connector healthy.
- Given the change, when `uv run pytest` runs, then everything passes with 24 skipped.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all pass, 24 skipped
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept

## Suggested Review Order

**Where a rotated token goes**

- The store pm-ai's startup code gives the Graph connector: reads live, one lock, thread-safe.
  [`wiring.py:926`](../../../../pm_ai/app/wiring.py#L926)

- The one writer: updates one connector's entry, keeps everything else.
  [`connector_enrolment.py:340`](../../../../pm_ai/core/connector_enrolment.py#L340)

- One rule for "is this entry this connector's", used by both read and write-back.
  [`connector_enrolment.py:170`](../../../../pm_ai/core/connector_enrolment.py#L170)

**What the connector does with it**

- Saves before use; takes the lock only when Microsoft actually issued a new token.
  [`auth.py:1202`](../../../../pm_ai/connectors/graph/auth.py#L1202)

- A store that cannot be read becomes a custody failure, not a bug.
  [`auth.py:1136`](../../../../pm_ai/connectors/graph/auth.py#L1136)

- The store's own errors, so the connector never sees storage's vocabulary.
  [`auth.py:275`](../../../../pm_ai/connectors/graph/auth.py#L275)

- Busy, missing and unsaved cases each get an honest remedy.
  [`auth.py:783`](../../../../pm_ai/connectors/graph/auth.py#L783)

**Tests**

- The acceptance case: a rotation in one run is the token the next run uses.
  [`test_rotated_tokens_are_kept.py:164`](../../../../tests/slice/test_rotated_tokens_are_kept.py#L164)

- Nothing else in the credentials file changes.
  [`test_rotated_tokens_are_kept.py:203`](../../../../tests/slice/test_rotated_tokens_are_kept.py#L203)
