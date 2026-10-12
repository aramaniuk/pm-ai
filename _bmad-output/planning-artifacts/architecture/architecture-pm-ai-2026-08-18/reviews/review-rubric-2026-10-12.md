# Rubric review — AD-5 "Re-entry is per thread" amendment (2026-10-12)

Target: `ARCHITECTURE-SPINE.md` AD-5, the sentences added after "`ArtifactBusy` is the one refusal…" (diff against HEAD on `docs/claim-reentry-per-thread`). The amendment replaces the 2026-10-11 owed item ("a second claim on a path the *process* already holds is a programming error… owed by `8q`") with a ratified per-thread rule.

Code checked: `pm_ai/domain/claims.py` (`ClaimReentered`), `pm_ai/platform/claims.py` (`exclusive`, `_HELD_HERE`), `pm_ai/storage/service.py` (`_canonical`, `_claimed_here`, `StorageService.exclusive`, `holds_claim`), `pm_ai/ports/__init__.py` (`ClaimPort.exclusive`, `StoragePort.exclusive`, `StoragePort.holds_claim`), `pm_ai/app/wiring.py` (`SealedRefreshTokenStore.exclusive` / `write`), `pm_ai/core/connector_enrolment.py` (`record_user_id`), `pm_ai/connectors/graph/auth.py` (`_claimed`), `pm_ai/connectors/graph/__init__.py` and `pm_ai/app/pipelines.py` (catch-alls), `pm_ai/domain/harvest.py`, and `tests/architecture/test_storage_capabilities.py` (re-entry tests).

**Verdict: PASS with two findings worth acting on (one medium, one low-medium) and three low ones.** Every factual claim in the new sentences matches the code. The rule is enforced at three points and covered by tests. The gaps: the stated asyncio limit names the harmless half of the failure and leaves out the harmful half, and "never retried" holds only because of a constant elsewhere, not because of the type.

---

## 1. Factual claims, sentence by sentence

| Claim in the amendment | Code | Holds? |
|---|---|---|
| A second claim on a path the same thread already holds raises `ClaimReentered` | `StorageService.exclusive` checks `canonical in _claimed_here()` (thread-local set) and raises `ClaimReentered`; `platform/claims.exclusive` does the same against `_HELD_HERE`; `SealedRefreshTokenStore.exclusive` checks `_holder == threading.get_ident()` | Yes |
| a programming error | `ClaimReentered(RuntimeError)`, deliberately not a subclass of `ClaimHeld`; message says "That is a bug in pm-ai" | Yes |
| raised before any directory is created | storage: the check runs before `_mkdir_enclave` / `mkdir` (and before the marker report, which only reads); platform: the check runs before `guarded.parent.mkdir`; tested by `test_a_re_entry_creates_no_directory` on both doors | Yes |
| never retried | `SealedRefreshTokenStore` catches only `ArtifactBusy` and `OSError` in its retry loop, so `ClaimReentered` escapes on the first attempt; it is raised before the loop anyway. Graph `_claimed` translates only `StoreBusy` / `StoreUnavailable`. Further out, the connector catch-alls turn it into a `HarvestFailure` with `retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE`, which is `False` | Yes, but see finding F2 |
| never `ArtifactBusy` | the façade checks before the port is asked, so a port that answers re-entry with `ClaimHeld` (the in-memory fake) cannot turn it into busy (`test_a_re_entry_is_refused_whichever_claim_port_is_wired`) | Yes |
| A claim held by another thread of the same process is ordinary contention and refused as `ArtifactBusy` | separate `os.open` descriptors are separate open file descriptions, so `flock` conflicts between threads → `ClaimHeld` → `ArtifactBusy`; tested via `held_by_another_thread` | Yes at the storage door. The sealed store answers `StoreBusy` after its wait (F4) |
| `StoragePort.holds_claim` answers whether this thread holds one | declared on `StoragePort` (ports `:747`); implemented on the same thread-local set as the guard; `test_holds_claim_answers_for_this_thread_only` | Yes |
| The guard is thread-local, so asyncio tasks sharing one thread are indistinguishable; a task claiming a path another task on its thread holds across an `await` gets `ClaimReentered` | `threading.local()` in both modules; `_holder` is a thread ident | Yes, but incomplete (F1) |
| No async claimant exists today | no `asyncio` claimant calls `exclusive` in `pm_ai` (the daemon is unbuilt) | Yes |

No claim in the new text contradicts the code.

## 2. Findings

### F1 — Medium: the asyncio limit names the safe failure and leaves out the unsafe one

The spine says the only consequence of a thread-local guard under asyncio is that a second task "gets `ClaimReentered`". That half is loud and safe. The other half is quiet and unsafe. `holds_claim` reads the same per-thread set, so a task that holds **nothing** gets `True` while another task on its thread holds the claim:

- `record_user_id(..., claimed=True)` checks `storage.holds_claim(...)` precisely to refuse a caller that says it holds the claim when it does not. Under asyncio, task B passes that check while task A holds the claim across an `await`. B then reads and writes `connectors/<instance>.json` without serializing against A, which is the exact read-modify-write the claim exists to prevent.
- `SealedRefreshTokenStore.write` makes the same check on `_holder == threading.get_ident()`. It has the same hole: one task's write is accepted inside another task's claim.

So the amendment presents the limit as "a spurious programming error", when it is also "the guard against unserialized writes is bypassed". The two units that would diverge are `record_user_id` (and the token store) and whatever async claimant `4e` adds. Fix in the spine: add one clause, for example "…and `holds_claim` answers yes for every task on the holding thread, so a `claimed=True` write from a task that does not hold the claim is not refused." Keep `4e` as the owner. The mechanism `4e` must replace covers both halves (a `contextvars` holder, or a rule that claims are never held across an `await`).

### F2 — Low-medium: "never retried" is a property of a constant, not of the type

`ClaimReentered` does reach the harvest path. A graph token refresh inside a claim would raise it out of `access_token`. It is not retried today only because the connector and pipeline catch-alls (`graph/__init__.py` around `:644`/`:667`, `pipelines.py` around `:568`) map **any** unclassified exception to `retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE`, which is `False` (`domain/harvest.py:29`). Flip that constant, and re-entry becomes a scheduled retry that meets itself every time, which is the failure the rule exists to prevent. The rule as written cannot be enforced against that change. Either the spine says why it holds ("…never retried: the scheduler treats an unclassified fault as non-retryable, and re-entry must stay unclassified or be classified non-retryable"), or the catch-alls get a named `ClaimReentered` branch that is non-retryable whatever the constant says. A one-clause spine fix is enough.

### F3 — Low: the code's own docstrings still say "per process"

The spine now says per thread, but the code's prose still says the older thing:
- `pm_ai/domain/claims.py` `ClaimReentered` docstring: "AD-5 (2026-10-11, story 8q): a claim is never taken twice by one process." The same docstring then says another thread is contention, so it contradicts itself, and the date predates the decision (2026-10-12).
- `tests/architecture/test_storage_capabilities.py` section header: "A claim is never taken twice by one process (AD-5, story 8q)".

Behaviour matches the spine. Only the wording diverges. It is worth a one-line follow-up so the next reader of the code does not re-derive the per-process rule.

### F4 — Low: "refused as `ArtifactBusy`" is the storage-door answer; the sealed store answers differently

For `config.json` through `SealedRefreshTokenStore`, another thread's hold shows up as one of two things. With the same store object, the `_lock` is busy, which gives a synthetic "another caller in this process…" message. With a different object, the answer is `ArtifactBusy`. Either way the store waits it out and finally raises `StoreBusy`, not `ArtifactBusy`. The spine's later sentence ("No caller waits except the sealed credential store") covers the wait. The memlog decision carries "(the sealed credential store waits it out)", but the spine sentence dropped it. Restoring that parenthetical makes the two sentences read as one rule. No unit diverges today.

### F5 — Low (observation): three guards implement one rule

The spine says "the guard" (singular). The code has three: the storage façade's `_CLAIMED_HERE`, the platform adapter's `_HELD_HERE`, and the sealed store's `_holder`. They agree today: the same key (`parent.resolve() / name`), the same thread scope, and the same before-any-mkdir ordering. Tests pin the first two against each other (`test_the_platform_lock_held_by_the_facade_refuses_a_direct_re_entry`). The redundancy is defensible, because the façade check makes a fake port unable to turn re-entry into busy. But a future change to one key (for example resolving the full path to follow a symlinked file) would make `holds_claim` and the platform guard disagree. Optionally the spine could say "each door checks, keyed as the platform lock keys it". This is not required.

## 3. Consistency with the rest of the spine

Searched the spine for claim, `ArtifactBusy`, `ClaimHeld`, re-entry/re-entrant, "already holds", `holds_claim`, `StoreBusy`, "same process", "owed".

- The removed owed item ("…the process already holds… owed by `8q`") does not survive anywhere else in the spine, and no Open Risks row still carries it. The 2026-10-11 memlog event still lists it as owed, which is correct as history, and the new memlog decision closes it.
- AD-39's custody bullet (`:532`) and the closed Open Risks row on two custodians (`:1125`) say the token store writes under the artifact's exclusive claim. They are consistent: `claimed=True` writes under the caller's claim and does not re-take it.
- The "There is no daemon" risk row (`:1116`) and the `4e` open item agree with the asyncio deferral.
- AD-37's per-target lock (`:500`) is a different mechanism (proposal execution) and is untouched.
- No contradictions found.

## 4. Does the rule prevent its stated divergence?

The stated divergence: one thread re-entering, being told busy, blaming a nonexistent process, and retrying itself forever. It is prevented for every synchronous caller that exists today, at both doors and at the token store, and tests check it. The rule leaves two things open. Async callers are owned by `4e`, but the owner is told only half the failure (F1). Retry-safety under a changed catch-all policy is not named (F2).

## 5. Recommended edits (spine, via the skill)

1. Extend the asyncio sentence with the `holds_claim` / `claimed=True` half (F1).
2. Say why "never retried" holds, or require a named non-retryable classification (F2).
3. Restore "(the sealed credential store waits it out)" after the `ArtifactBusy` sentence (F4).

Code follow-ups, outside the spine: the per-process wording in the `ClaimReentered` docstring and the test section header (F3).
