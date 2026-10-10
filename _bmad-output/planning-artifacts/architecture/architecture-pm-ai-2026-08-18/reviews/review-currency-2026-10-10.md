# Currency review — ARCHITECTURE-SPINE.md amendments tagged 2026-10-09

Reviewer: currency lens, architecture reviewer gate. Date: 2026-10-10.
Scope: every passage the working-tree diff touches (`git diff -U0 HEAD` hunks at spine lines 92, 101-103, 173-174, 211, 239, 305-311, 366, 445-446, 523-531, 632, 694, 718, 1021, 1048, 1112-1121, 1151-1152). The Stack table (lines 733-800) was not touched by the diff; its `watchdog` row was re-verified anyway because the Open Risks row cites it.

Verdict: **PASS WITH FIXES** — every environment, version, date and attribution claim was measured and holds; two code claims were asserted rather than checked (one materially wrong about which mechanism raises `ArtifactBusy`, one present-tense for a field that does not exist yet), plus three wording imprecisions.

## 1. Claims about the code

| Spine passage | Claim | Checked against | Result |
|---|---|---|---|
| AD-5 line 173 | "what holds it today is `exclusive()` in `platform/claims.py` — a `flock` on a sidecar the claim deliberately never unlinks" | `pm_ai/platform/claims.py:46-76` | **Holds** for that function: `fcntl.flock(LOCK_EX\|LOCK_NB)` on `<guarded>.lock`, docstring explains why it is never unlinked. |
| AD-5 line 173 | "`ArtifactBusy` is therefore a real condition and a command that meets it reports it by name" | `pm_ai/storage/service.py:1302-1354`; `pm_ai/domain/claims.py`; `pm_ai/app/wiring.py:89,1612`; `pm_ai/surfaces/cli/dispatch.py:59,961,1180,1309,1434,1568` | **FINDING F1 — wrong mechanism.** `platform/claims.exclusive` raises `ClaimHeld` (`pm_ai/domain/claims.py:20`) and guards only the project registry (`wiring.py:1612`, story 4k). `ArtifactBusy` is raised by a *different* function, `StorageService.exclusive` (`service.py:1342-1354`), which uses an `O_CREAT\|O_EXCL` `.claim` file that **is unlinked on release** (`claim.unlink(missing_ok=True)`, line 1354) and whose refusal text tells the operator to delete an orphaned claim — the exact design `claims.py:10-16` argues against. The paragraph therefore names one mechanism where the code has two with opposite orphan semantics, and the "therefore" attributes `ArtifactBusy` to the one that cannot raise it. The gap row at line 1112 ("`fcntl.flock` via `platform/claims.py`"; "`ArtifactBusy` is caught in three CLI places") carries the same conflation, and memlog entry 333 repeats it verbatim — so this was asserted, not read. Fix: say that two claims exist — the registry's `flock` (`ClaimHeld`, `platform/claims.py`, never unlinked) and the sealed-artifact `O_EXCL` claim (`ArtifactBusy`, `storage/service.py`, removed on release, orphaned by a kill) — and either record the second as the admitted deviation from `claims.py`'s own rationale or assign slice `4e` to unify them. Catch-site count: `dispatch.py` has three `ArtifactBusy` sites (961, 1434, 1568) and two `ClaimHeld` sites (1180, 1309); `entry.py:419` and `wiring.py:1299` also catch `ArtifactBusy`. |
| AD-5 line 173 | "an append relies on the record rule instead (AD-47)" | `service.py:1356` `append_event_log` takes no claim | Holds. |
| Identifiers row line 718 | "`evt_` is a prefix plus random hex (`storage/service.py`)" | `service.py:300-304` `_ulid()` → `"evt_" + secrets.token_hex(10)` | Holds. (Note for the companion file, not the spine: `deferred-work.md:61,289` cite `service.py:215`; the function is at line 300.) |
| Identifiers row | "no reader pages by id" | `grep ORDER BY / sorted(` over `storage/` and `core/` — only `(start, meeting_id)` sorts in `meeting_records.py:598`, `rendering.py:482` | Holds. |
| AD-3 line 103, AD-44 line 632, gap row 1115 | "The flip at `scope_model.py:540,733` is owed by `9a`" | `pm_ai/domain/scope_model.py:540` and `:733` are both `File("daily_dashboard.md", Tier.TRUTH, ...)` | Holds — line numbers exact, still Tier 1 in code, 9a is wave 2 (`stories.yaml:392`). |
| AD-9 line 211 | "`HarvestResult` carries … and — `[2026-10-09, slice 8p]` — `answered_at`, the instant the provider answered, which every result that reached the provider carries" | `pm_ai/domain/harvest.py:272-311` fields: `events, cursor, outcome, coverage, failure, refusals, records` — **no `answered_at`**; `grep answered_at pm_ai tests` → nothing; only the 8p story file (`stories/8p-…md:71`, unchecked box) names it | **FINDING F2 — present tense for an unbuilt field.** The memlog (entry 342) says the list "gains" it under slice 8p, which is a decision; the spine sentence reads as fact. Every other 2026-10-09 passage that depends on an unbuilt slice says "owed by"/"built by"; this one should too. Fix: "…and, once slice `8p` lands, `answered_at` …" or move the sentence under a `*Not yet built*` marker like AD-11's. |
| AD-11 line 239 | "Built 2026-10-08 by story `4m`"; "parsed once, before composition"; "a named project wins over the folder"; "an unenrolled name is refused naming the enrolled projects"; "`doctor`'s `project selection` line say how the choice was made" | `git log`: `3d4071c 2026-10-08 fix(4m)`; `entry.py:33` ("`main` parses `argv` once, before composing"); `entry.py:581,611,779-789`; `entry.py:519,1015` (`SELECTION_PROBE = "project selection"`); `dispatch.py:1749` | Holds. |
| AD-11 line 239 | "with no project enrolled at all, commands aimed at the personal or application scope are still refused before their target is read" | `entry.py:15,187-192,299`; `dispatch.py:187` ("composition stops at 'no project enrolled'") | Holds as the current behaviour; 4o is wave 2 (`stories.yaml:270`, "Andrei confirmed"). |
| AD-11 line 239 | "only a command without subcommands parses `--scope`, so a leaf such as `connector add` cannot take it" | `dispatch.py:1895-1935` parses options against a per-command `allowed` set; the leaf table was not traced to the end | **Not independently verified** — plausible, no evidence against it. Low stakes; noted so the next reviewer does not assume it was checked. |
| AD-39 line 528 | "the sealed store reads this instance's entry each time it is asked and writes a rotation back through `replace_credential`, a busy file is retried briefly and then reported" | `wiring.py:1200-1234` `SealedRefreshTokenStore` docstring and fields (`attempts`, `interval`, "about two seconds before `StoreBusy`"), `:1259` calls `replace_credential`, `:1292-1312` retry loop on `ArtifactBusy` | Holds. |
| AD-39 line 528 | "the in-memory store that made a rotation non-durable is gone" | `auth.py:420` `InMemoryRefreshTokenStore` still exists; `auth.py:685` says it "used to be the `default_factory`"; `wiring.py:962-966,1089` still uses it deliberately during `connector add graph`'s sign-in, before anything is sealed | **FINDING F3 — overstated.** The class is not gone; what is gone is its role as the silent default custody. The sign-in path keeps it on purpose (nothing is sealed until the health check passes). Fix: "the in-memory store is no longer the default custody — a daemon-wired connector gets the sealed store, and the in-memory one survives only inside `connector add`'s sign-in, before the first seal". |
| AD-39 line 528, gap row 1121 | `replace_credential` "refusing a missing instance or a system mismatch" | `pm_ai/core/connector_enrolment.py:422-478`: two `CredentialNotHeld` raises, write skipped when unchanged | Holds. |
| AD-39 line 531 | "`doctor` … lists the enrolled instances by name (slice `4n`)" | `stories.yaml:270` lists 4n in wave 2 | Holds as owed. |
| AD-21 line 311 | exempt list names `connector sign-in` | `grep sign-in dispatch.py` — no such command; it is slice 8m (`stories.yaml:378`) | **FINDING F4 — minor.** One unbuilt command sits in a list of nine built ones with no marker. All other names (`connector check`, `connector add`, `setup`, `dashboard`, `doctor`, `project add`, `goal set`, `key enrol`, `config show`) exist in `dispatch.py`. Fix: "`connector sign-in` (slice `8m`)". |
| AD-33 line 445 | "`mtg_` plus random hex"; "Built by `33e` and `11b`" | `grep mtg_ pm_ai` → only `domain/identity.py` comments; both slices wave 2 | Holds as owed; nothing in code contradicts it. |

## 2. Claims about the environment (Open Risks lines 1151-1152)

Measured 2026-10-10 on the development machine:

- `uv run python -c "import platform,sys;print(platform.machine(),sys.version)"` → `x86_64 3.14.7 (main, Aug 5 2026, 15:34:57) [Clang 22.1.3]` — matches "x86_64 build", "Python 3.14.7".
- `uname -m` → `arm64`; `sysctl machdep.cpu.brand_string` → `Apple M3 Pro`; `hw.memsize` → 19 327 352 832 B (18 GiB) — matches "Apple M3 Pro with 18 GB".
- `uv python list --only-installed` → `cpython-3.15.0rc1-macos-x86_64-none`, `cpython-3.14.7-macos-x86_64-none`, `cpython-3.13.14-macos-x86_64-none` (×2 paths) — matches "only `macos-x86_64-none` builds of 3.13, 3.14 and 3.15".
- `uv run python -c "import watchdog, _watchdog_fsevents; print('ok')"` → `ok`, `watchdog 6.0.0` from `.venv/lib/python3.14/site-packages` — matches "built it from source under Python 3.14.7 … and `_watchdog_fsevents` loaded".
- `xcode-select -p` → `/Applications/Xcode.app/Contents/Developer` — matches "with Xcode present".
- `deferred-work.md:919-920` records the same measurements dated 2026-10-09 — matches "Recorded in `deferred-work.md`".

Both rows hold in full.

## 3. Claims about versions

- Stack row line 759 (`watchdog ==6.0.0`) was **not** amended by this diff; re-verified because line 1151 relies on it. PyPI JSON fetched 2026-10-10: `info.version = 6.0.0`; newest releases 5.0.3 (2024-09-27), 6.0.0 (2024-11-01); 6.0.0's macOS wheels are cp39–cp313 and PyPy only; **no `cp314` wheel in any release**. So "still the latest", "macOS wheels stop at 3.13", "latest release is 2024-11-01, so ~23 months without one" (23 months and 9 days today) all hold. `pyproject.toml:91` and `uv.lock:1201-1214` pin `==6.0.0`. Optional: the row's "still the latest on 2026-09-25" could be re-dated 2026-10-10, but the row was not in scope.
- No other Stack row text was touched (diff hunks confirm). `sqlite-vec ==0.1.9` in `pyproject.toml:38` matches the row; not re-checked on PyPI since untouched.

## 4. Dates and attributions vs. the memlog (entries 331-342, all dated 2026-10-09)

| Spine passage | Tag | Memlog | Result |
|---|---|---|---|
| AD-3 line 103, gap row 1115, Deferred 1048, AD-44, AD-47 | `[2026-10-09, Andrei]` | 340 `[ADOPTED, Andrei]` step 4 | Match |
| AD-5 line 173, gap row 1112 | `[2026-10-09]` | 333 | Match (same wrong mechanism, see F1) |
| AD-9 line 211 | `[2026-10-09, slice 8p]` | 342 "gains answered_at (slice 8p)" | Date matches; tense does not (F2) |
| AD-11 line 239 | "Built 2026-10-08 by 4m"; "Andrei confirmed on 2026-10-09" | 336, 342; git `3d4071c` 2026-10-08 | Match |
| AD-21 lines 305-311 | `[revised 2026-10-09]`, `[2026-10-09, Andrei]` | 341 `[ADOPTED, Andrei]` step 5 | Match |
| AD-27/AD-48 lines 366, 1021 | `[reassigned 2026-10-09; was 33d]` | 337 | Match |
| AD-33 line 445, gap row 1120 | `[2026-10-09, Andrei]` | 339 `[ADOPTED, Andrei]` step 3 | Match |
| AD-39 lines 523-531, gap row 1121 | `[2026-10-09, story 8j]`, "Closed 2026-10-08 by story 8j" | 332, 338; git `4f3b066` 2026-10-08 | Match |
| Identifiers line 718 | `[2026-10-09]`, "(story 2f)" | 334 "Measured on 2026-08-29 (story 2f)"; `deferred-work.md:289` | Match |
| Open Risks 1151-1152 | "Installed 2026-10-09", `[2026-10-09]` | 335 | Match |

Memlog header `updated: 2026-10-10T00:22` is consistent with a run that ran past midnight; the entries themselves are dated 2026-10-09, which is what the spine tags use. No attribution in the amended passages lacks a memlog entry, and no memlog decision of 2026-10-09 is missing from the spine.

## Findings, ranked

1. **F1 (AD-5 line 173; gap row 1112; memlog 333) — wrong mechanism for `ArtifactBusy`.** `platform/claims.exclusive` is a `flock` raising `ClaimHeld` for the project registry only; `ArtifactBusy` comes from `StorageService.exclusive`'s `O_EXCL` claim file, which is unlinked on release and orphaned by a kill — the design `claims.py` rejects. Document both or assign `4e` to unify.
2. **F2 (AD-9 line 211) — `answered_at` stated as carried; it does not exist** (`harvest.py:297-311`). Mark it owed by `8p`.
3. **F3 (AD-39 line 528) — "in-memory store is gone" overstates**: `InMemoryRefreshTokenStore` remains and is used deliberately in `connector add graph`'s sign-in (`wiring.py:962,1089`). Say it is no longer the default custody.
4. **F4 (AD-21 line 311) — `connector sign-in` listed among built commands** without its `8m` marker.
5. **F5 (AD-11 line 239) — the leaf `--scope` claim was not verified** by this review; low stakes, recorded so it is not mistaken for checked.

Everything else in the amended passages — line numbers, environment measurements, versions, dates, attributions — was reality-checked and holds.
