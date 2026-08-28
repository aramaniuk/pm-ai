# Reviewer — technology currency and reality check (2026-08-27 update)

**Verdict: PASS with three corrections applied.** Every technology this update binds was checked against the web or the repository, not asserted. Two factual claims in the new ADs were wrong or unverified and are fixed.

## Verified

| Claim | How checked | Result |
|---|---|---|
| `watchdog` is the FSEvents-capable watcher and its current version | PyPI JSON, fetched 2026-08-27 | **6.0.0**, released 2024-11-01. macOS FSEvents + kqueue backends. Requires Python ≥3.9 against this project's ≥3.13. |
| `sqlcipher3` is genuinely gone | `pyproject.toml` | Absent from every dependency list; only a comment explaining its removal remains. |
| Exactly two artifacts are encrypted | `pm_ai/domain/scope_model.py` | Exactly two `encrypted=True` declarations: `config.json`, `telegram_cache/`. |
| The pre-written suite expects an unbuilt `pm_ai.core.scheduler` | `tests/architecture/test_domain_invariants.py:147`, `ls pm_ai/core/` | True. The module is absent, so `mod()` turns that test into a skip. |

## Findings

**C1 — MEDIUM, applied. AD-45 said `run_harvest` and `run_transcript_ingestion` are "functions nothing calls." That is false.** They are called extensively by `tests/slice/test_vertical_slice.py`, `test_transcript_slice.py` and `test_r4_gate_fixes.py`. The true and still-damning statement is that **no production caller exists** — only the slice tests drive them, which is what makes them jobs without a job runner. A spine that overstates a fact invites a reader to check it and find the spine wrong.

**C2 — MEDIUM, applied. An unverified capability claim.** The Stack row asserted watchdog "offers no historical replay from a persisted event id." The PyPI metadata does not establish that, and AD-46 rejects replay on its own grounds regardless. Rewritten so the spine states its own decision rather than a library's absence.

**C3 — LOW, applied. `pm_ai/core/jobs.py` already exists** — idempotency keys only (AD-20) — and the source tree did not name it, so a reader seeing the new `core/scheduler.py` entry could reasonably think one duplicates the other. Both are now named with their distinct jobs.

## Note carried forward, not a finding

`watchdog` 6.0.0 is ~21 months without a release as of this run. That is the same shape of standing supply risk the Stack already records for `sqlite-vec`, though over a far smaller surface and behind a port (AD-46) that makes replacement local. Recorded in the Stack row rather than as a finding.
