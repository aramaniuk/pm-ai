---
title: 'A Graph sign-in records which Microsoft user signed in'
type: 'feature'
created: '2026-10-10'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing records which Microsoft user a Graph connector signed in as.

A Graph connector's settings row holds the app id, the tenant and the harvest widths, and the sealed store holds the token — but neither says whose calendar and messages this is. Story 23c, which marks the messages that mention the PM, needs the PM's own Graph user id to tell a mention of the PM from any other; without it the mark cannot be made at all. And the re-sign-in command of story 8m needs the same id to refuse a sign-in by a different person than the one the connector was enrolled for.

**Approach:** Every Graph sign-in reports the signed-in user's Graph user id, and the connector's settings row records it under `user_id`. `connector add graph` writes it at enrolment; `connector sign-in` (story 8m, which lands after this one) writes it through the same one-key row update. The consumers are 23c, which reads the key from the row, and 8m, which compares it.

## Boundaries & Constraints

**Always:**
- **The user id is Microsoft's immutable identifier of the signed-in account**, the value Graph returns as a user's `id`, taken from the sign-in itself — not the per-application subject, not the sign-in name, both of which can differ or change.
- **When the sign-in does not carry that identifier**, the first part of the two-part account id MSAL reports is used, and only when MSAL reports exactly one account and its id has the two-part `<user>.<tenant>` shape. Otherwise the sign-in is refused naming the missing identifier, and nothing is sealed or written: an account pm-ai cannot name is not a Graph user it can harvest for.
- **`connector add graph` records it** beside the app id, tenant and widths it already writes, in the same row, in the same write.
- **One row update sets `user_id` and nothing else.** It re-reads the row at write time and changes only that key, so every other key — a category mapping hand-added since the row was first written included — is kept as it was. This is the writer `connector sign-in` uses.
- **A row whose `user_id` is missing, blank, whitespace or not text has no recorded user.** Any user may sign in for it and is recorded. A reader treats such a value as absent and never compares it — the rule 23c applies when it reads the key.
- **A sign-in by a different user than the row records is refused by name, saving nothing**, wherever a sign-in is run against an existing row: a different person is a different connector, and accepting it would harvest another person's calendar into the PM's tree. Today only `connector sign-in` runs against an existing row, so the refusal is exercised there.
- **The user id is not a secret** and may appear in the row, in `doctor`'s row problems and in refusals; the sign-in token still never appears anywhere.

**Ask First:** Nothing.

**Never:**
- No change to the credentials file's layout or the sealed sign-in's shape: the user id lives in the settings row, not beside the token.
- No new command, no change to the four questions `connector add graph` asks, no change to which rows the daemon's start builds — a row without `user_id` still builds.
- No reading of the key by any consumer here; 23c and 8m are the readers.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| `connector add graph` | a fresh add, the sign-in carries the user id | the row carries `user_id` equal to it, beside the app id, tenant and widths | N/A |
| User id from the account list | the sign-in carries no user id; MSAL reports exactly one account with a two-part id | `user_id` is the first part of that id | N/A |
| Account without a user id | no user id in the sign-in; no account, several, or an id that is not two-part | refused naming the missing identifier; nothing sealed or written | exit 3 |
| The row update | a row with a hand-added `categories` mapping; `user_id` set | the row afterwards carries the mapping, every other key as it was, and the new `user_id`; a second update with the same id changes no bytes | N/A |
| Row with no usable `user_id` | key absent, `""`, `"  "`, or a number | read as absent; a sign-in by any user is accepted and the id recorded | N/A |
| Different user | row `user_id` set; a sign-in's user id differs | refused naming the recorded and the signed-in user; nothing written | exit 3 |
| Same user again | row `user_id` set; the sign-in's matches | accepted; the row's `user_id` unchanged | N/A |
| Daemon start | a row enrolled before this slice, no `user_id` | the connector still builds; no warning about the key | N/A |
| Token never on screen | every row above | the sign-in token in no output | N/A |

</frozen-after-approval>

## Code Map

- `pm_ai/connectors/graph/__init__.py:218-222` -- the row keys; add `USER_ID_KEY = "user_id"`
- `pm_ai/connectors/graph/auth.py:724` -- `sign_in`; at 799 the sealed credential takes `home_account_id=self._enrolling_account(app, result)`. `_enrolling_account` 1238 already prefers the id token's `oid`/`tid` (1247-1249) and falls back to exactly one cached account (1250-1252), refusing with `CredentialStale` otherwise (1259). It returns `"{oid}.{tid}"`; the user id is its first segment, and a cached `home_account_id` is accepted only when it splits into two non-empty parts. `SealedCredential` (366-377) keeps `home_account_id`, unchanged
- `pm_ai/connectors/graph/auth.py:41` -- `profile`, which `oid` needs, is among MSAL's reserved scopes
- `pm_ai/app/wiring.py:938` -- `GraphSignedIn.settings` is what the CLI hands to `enrol_connector`; `sign_in` (1067) builds it at 1165-1170 from `CLIENT_ID_KEY`, `TENANT_KEY`, `WIDTH_KEY`, `REACH_BACK_KEY` — add `USER_ID_KEY`
- `pm_ai/surfaces/cli/dispatch.py:840` -- `enrol_connector(..., settings=signed.settings)`; nothing else in the surface learns the key
- `pm_ai/core/connector_enrolment.py:282` -- `enrol_connector` composes the row from `settings` (reserved-key clash check 340; `RESERVED_ROW_KEYS` 121 does not name `user_id`). The one-key update belongs beside `assert_enrollable` (239): read the row with `_decode` (548), set the key, write with `_encode` (596), returning whether bytes changed. A pure comparison of a recorded id with a signed-in one — absent-when-blank — lives here too, for 8m to call
- `pm_ai/app/wiring.py:1380` -- `_enrolled_configurations`, the row reader 23c's reader (story 4n's) is built on; it reads the key off the mapping and applies the absent rule
- Tests: `tests/slice/test_connector_add_graph.py:47` `ACCOUNT_CLAIMS` (`oid`, `tid`), `_token` 62 (claims per answer), `Microsoft` 73, `_row` 250, the valid-add test 276; `tests/connectors/test_graph_auth.py:88` `FakeMsal`
- MSAL: `.venv/lib/python3.14/site-packages/msal/token_cache.py:413` builds `home_account_id` as `"{uid}.{utid}"`

## Tasks & Acceptance

**Execution:**
- [ ] `pm_ai/connectors/graph/__init__.py` -- add `USER_ID_KEY` -- one spelling for the row key
- [ ] `pm_ai/connectors/graph/auth.py` -- `sign_in`'s result exposes the user id: `oid` when the claims carry it, else the first part of the one cached `home_account_id` when it is two-part, else the existing `CredentialStale` refusal naming the missing identifier -- the source rule in one place
- [ ] `pm_ai/app/wiring.py` -- `GraphSignedIn.settings` carries `USER_ID_KEY` -- `connector add graph` writes it with no surface change
- [ ] `pm_ai/core/connector_enrolment.py` -- `record_user_id(storage, instance=, user_id=)`: re-read, set one key, write, report whether bytes changed; `recorded_user_id(row) -> str | None` applying the absent rule; `assert_same_user(recorded, signed_in)` raising a typed refusal naming both -- the writer and the comparison 8m and 23c share
- [ ] `tests/slice/test_connector_add_graph.py` -- the valid-add test asserts `user_id`; `test_a_sign_in_without_oid_takes_the_one_cached_account_id`; `test_a_sign_in_naming_no_account_is_refused_and_seals_nothing` (no claims; two accounts; a one-part id) -- the add rows
- [ ] `tests/core/test_connector_enrolment.py` -- the one-key update keeps a hand-added mapping and is byte-stable on repeat; the absent rule over `""`, whitespace, a number and a missing key; the comparison refuses a different user and accepts the same -- the writer rows
- [ ] `tests/slice/test_every_enrolled_project.py` or `tests/app/` -- a pre-slice row without `user_id` still builds -- the start is untouched

**Acceptance Criteria:**
- Given a fresh `connector add graph` with an approved sign-in, when the row is read back, then it carries `user_id` equal to the sign-in's `oid`, and the sealed credential's shape is unchanged.
- Given a sign-in whose claims carry no `oid` and whose MSAL cache holds one two-part account id, when the add completes, then `user_id` is that id's first part; given no usable account, then the add is refused naming the missing identifier and nothing is sealed or written.
- Given a row carrying a hand-added `categories` mapping, when `user_id` is recorded on it, then the mapping and every other key are unchanged.
- Given the change, when `uv run pytest` runs, then everything passes and the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09).

## Design Notes

The user id is the `oid` claim of the sign-in's id token: Microsoft documents it as the immutable identifier of the user account and the value Graph returns as a user's `id` ([ID token claims reference](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference)). Not `sub`, which differs per application; not `preferred_username`, which changes. The fallback is MSAL's `home_account_id`, `"{uid}.{utid}"`; an ADFS-shaped id without that shape is refused rather than recorded, because a value that is not a Graph user id would make 23c's comparison silently false forever.

## Verification

**Commands:**
- `uv run pytest tests/slice/test_connector_add_graph.py tests/core/test_connector_enrolment.py -q` -- expected: all pass
- `uv run pytest -q` -- expected: all pass; the skip count is unchanged from the baseline at the slice's start (24 on 2026-10-09)
- `uv run mypy` -- expected: no errors
- `uv run lint-imports` -- expected: all contracts kept
