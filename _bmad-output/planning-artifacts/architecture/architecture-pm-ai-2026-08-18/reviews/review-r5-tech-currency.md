# R5 — Technology Currency & Reality-Check Review

- **Target:** `ARCHITECTURE-SPINE.md` (`updated: 2026-08-22`) — the whole Stack table and every named technology, with weight on the 2026-08-22 amendments: **AD-43**, **AD-44**, AD-1 class L, AD-3, the ports inventory, two Consistency Conventions rows, and the Deferred section.
- **Review date:** 2026-08-22
- **Lens:** was each committed decision *web-researched or reality-checked*, or asserted from training data? Current library versions; does each named technology still exist and fit; are the live defaults of anything it leans on still true.
- **Method:** three evidence classes, in this order of trust —
  1. **Local empirical execution.** Every AD-43 git claim re-derived in throwaway repositories, each verdict cross-checked against *ground truth* (`git add` then `git diff --cached` — would git actually stage a file inside). `enable_load_extension` probed on all eight interpreters present on this machine.
  2. **Primary sources.** PyPI JSON API for every pinned package; GitHub releases/CHANGELOG/CMakeLists for whisper.cpp, sqlite-vec, pygit2; `platform.claude.com` docs for every Anthropic API and pricing claim; `ollama.com` library tags; `docs.python-telegram-bot.org`.
  3. **Repository state.** `pyproject.toml`, `uv.lock`, `.importlinter`, `tests/architecture/`, and the implemented `pm_ai/platform/vcs.py` treated as the reality this revision claims to be aligned with.
- **Prior context:** `review-r4-tech-currency.md` (2026-08-19) treated as an unverified assertion and re-derived, not inherited. Its 12 findings are dispositioned in §8.
- **Local git version:** `git version 2.50.1 (Apple Git-155)` (Apple's Xcode CLT fork).
- **Local Python:** project `.venv` is **CPython 3.14.7** (uv-managed, built 2026-08-05); `pyproject` declares `requires-python = ">=3.13"`.

---

## Verdict

**AD-43 is the best-researched decision in this document, and I could not break it.** All four of its git claims are true, and the prescribed spelling gave the correct answer in all ten repository configurations I tested against ground truth. The trailing-slash asymmetry in particular is not something training data volunteers — it is a thing you learn by running git — and the implemented adapter knows *why* it omits `--no-index`. Same for `enable_load_extension`: absent on all five stock interpreters here, present on all three uv-managed ones, exactly as claimed, in both directions. These two decisions were executed, not recalled.

**The Stack table, however, was not re-verified when the document was.** Its preamble still reads "re-verified against PyPI and `ollama.com` on **2026-08-19**" beneath a front-matter `updated: '2026-08-22'`. In those three days `anthropic` shipped `0.125.0` *and* **`1.0.0`** — a **breaking major**, on 2026-08-20, two days before this revision. The spine says of that one package: "**pin exactly** (`0.124.0` current) … **Never float**: `tool_runner` is a **beta** surface and AD-16 makes it load-bearing for the execution firewall." `pyproject.toml` declares it as bare `"anthropic[mcp]"` with **no specifier at all**. The single row the document is most emphatic about is the row that floats, and it floats into a major release.

**The most consequential *new* error is not a version at all.** This revision correctly extended the refusal and thinking hazards from Opus 5 to Sonnet 5 (fixing R4's F3/F4) — and then prescribed a remedy that does not exist on Sonnet 5. Server-side `fallbacks` is documented for **Claude Fable 5 and Claude Opus 5 only**. Briefings, drafts, and inquiry synthesis all run Sonnet 5 under AD-15. The router is told to "opt into server-side `fallbacks` so a decline is re-served rather than surfacing as a failed briefing" on precisely the path where that mechanism is unavailable. The hazard was correctly widened; the mitigation was widened with it, on assumption.

**And AD-1's amended class L asserts four constraints on the new git adapter, of which one is unimplemented and none is enforced** — in `pm_ai.platform`, the one package the enforcement suite exempts wholesale (`SHELL_ALLOWED = {"platform"}`). This is the same shape as the AD-1 gap the spine records as closed on 2026-08-19 for `pm_ai.app`: the layer permitted to shell out is the layer nothing scans. The remedy applied then was not applied when `platform` became the second class-L home three days later.

**Net:** the git and interpreter research is genuinely first-hand and holds up under execution. The vendor-side and registry-side claims are a mix of precisely-verified (prompt-cache floors, pricing, `llama3.1:8b` at 4.9 GB, `sqlcipher3-binary`'s wheel set) and three-days-stale on the row that matters most — plus one prescribed API feature that does not exist where it is prescribed.

---

## Severity summary

| # | Sev | Claim / area | Verdict |
|---|---|---|---|
| **F1** | **HIGH** | Stack: `anthropic` "**pin exactly** (`0.124.0` current) … Never float" | **STALE + CONTRADICTED BY THE REPO.** `1.0.0`, a breaking major, released 2026-08-20; `pyproject.toml` pins nothing |
| **F2** | **HIGH** | Anthropic notes: router "should opt into server-side `fallbacks`" for the Sonnet 5 briefing path | **UNAVAILABLE ON THAT PATH.** `fallbacks` is documented for Fable 5 and **Opus 5 only** |
| **F3** | **HIGH** | AD-1 class L: "allowlisted **absolute** binary path, argv list, `shell=False`, bounded timeout" | **UNENFORCED, one clause unimplemented.** `SHELL_ALLOWED = {"platform"}` exempts the only package that shells out; `GitVcs` uses `shutil.which("git")` off ambient `$PATH` |
| **F4** | **MEDIUM** | AD-43: "collapsing them to 'ignored' silently restores the third failure" | **REASONING WRONG, CONCLUSION RIGHT.** `check-ignore` consults the index by default, so the collapse fails *closed*. The real hazard is `--no-index`, unnamed in the AD |
| **F5** | **MEDIUM** | AD-43's table presented as the verified set ("Three cases verified against real git") | **INCOMPLETE.** Four further real configurations found; two publish a transcript, including a **nested `.gitignore`** negation and `-v` inverting the exit code |
| **F6** | **MEDIUM** | Stack preamble "re-verified … on 2026-08-19" in a document `updated: 2026-08-22` | **STALE BY CONSTRUCTION.** The amendment re-verified git, not the registry rows; `uvicorn` already disagrees with `pyproject` |
| **F7** | **MEDIUM** | AD-42.6: "class L stays whisper.cpp alone (AD-1)" | **CONTRADICTS the amended AD-1**, which added `git check-ignore` / `git ls-files` to class L in the same revision |
| **F8** | **MEDIUM** | Stack: "Python 3.13 (3.14 is the upgrade path)" | **STALE TWICE OVER.** 3.14 stable since 2025-10-07; the project venv is already 3.14.7; **3.13 goes security-only 2026-10-01**, ~6 weeks out, when 3.15 ships |
| **F9** | **MEDIUM** | Deferred / Stack: candidates `llama3.1:8b`, `qwen3:8b`; "the smallest `llama3.3` build is 26 GB" | **CANDIDATES AGING; NUMBER WRONG.** 26 GB is `q2_K`; `q4_K_M` is **43 GB**. Current-generation 8B-class is `granite4.1:8b` / `qwen3.5:9b` |
| **F10** | **MEDIUM** | Prerequisite: bridge lifecycle `initialize() → start() → start_polling()` | **WRONG OWNER AND WRONG ORDER.** `start_polling` is on `Application.updater`, and the documented order puts it *before* `start()` |
| **F11** | **LOW** | Ports inventory (amended row) lists 10 ports in `pm_ai.ports` | **HALF EXIST.** 5 declared; `ModelPort`, `KeychainPort`, `CryptoPort`, `SurfacePort`, `TranscriptSourcePort` absent — and AD-12's chokepoint leans on `ModelPort` |
| **F12** | **LOW** | "`temperature` / `top_p` / `top_k` are rejected (400)" | **NEEDS ONE WORD.** *Non-default* values are rejected; defaults are accepted. In SDK 1.x the params are removed entirely |
| **F13** | **LOW** | AD-43: "for a path that does not yet exist git answers *not ignored* for `…/transcripts`" | **OVER-GENERAL.** True only when the *rule* carries a trailing slash. Prescription still correct |
| **F14** | **LOW** | AD-43's repair instruction | **MISDIRECTS** in the nested-`.gitignore` case: names the root `.gitignore` when the negation is in a subdirectory |
| **F15** | **LOW** | `OLLAMA_NUM_PARALLEL=1` presented as a required fix (3 places) | **REDUNDANT** — already the documented default. Carried unfixed from R4 F9. `OLLAMA_MAX_LOADED_MODELS=1` *is* a real fix (default 3) |
| **F16** | **INFO** | `sqlite-vec` "standing supply risk" with no contingency named | **RISK CONFIRMED, CONTINGENCY MISSING.** There is no in-SQLite alternative: `sqlite-vss` is dead, DuckDB VSS is experimental with WAL data-loss caveats |
| **F17** | **INFO** | `sqlite-vec` needs `enable_load_extension`; stock macOS CPython lacks it | **CONFIRMED EMPIRICALLY**, 8 interpreters, in both directions |
| **F18** | **INFO** | `subprocess` invocation of `git` as the bound dependency | **CORRECT, and a library binding would be unsafe here.** libgit2 evidence in §7 |

**Confirmed as correct and current** (details in §5, §6, §9): `tool_runner` still at `client.beta.messages.tool_runner` and still beta in 1.0.0; the `[mcp]` extra; `claude-opus-5` / `claude-sonnet-5` as exact ids; $2/$10 and $5/$25 with the increase cancelled; thinking on by default on both; `max_tokens` covering thinking; assistant prefill rejected; `output_config.format` / `.effort`; `stop_reason: "refusal"` with `stop_details` at HTTP 200; **prompt-cache floors 512 Opus 5 / 1024 Sonnet 5 exactly**; whisper.cpp v1.9.x current with Metal default-on and Core ML build-time-only; `sqlcipher3` vs `sqlcipher3-binary`; `llama3.1:8b` at 4.9 GB; `python-telegram-bot` 22.8 with `run_polling()` still loop-seizing and `[job-queue]` still embedding APScheduler; `sqlite-vec` pre-1.0 with `0.1.10` stuck in alpha; FastAPI, keyring, APScheduler pins.

---

## 1. AD-43 — every git claim re-derived against real git

`git version 2.50.1 (Apple Git-155)`. Method: fresh `git init`, a `.gitignore` written, then three questions — `git check-ignore -q -- <path>`, `git ls-files -- <path>`, and **ground truth**: `touch` a file inside, `git add` it, check whether it landed in the index.

### 1.1 The four claims, one by one

**Claim A — "the rule, then `!/.project-ai/transcripts/` → git tracks; a text check reports protected."**
**CONFIRMED.** With `.gitignore` = `.project-ai/transcripts/` then `!/.project-ai/transcripts/`, `check-ignore -q` exits **1** (not ignored) and `git add .project-ai/transcripts/a.vtt` **succeeds** — the file is staged. A text matcher looking for the rule finds it and reports protected. A real leak, exactly as described.

**Claim B — "`.project-ai/` — a parent exclude → git ignores; a text check reports unprotected."**
**CONFIRMED**, and stronger than stated: it holds whether or not the directory exists on disk. `check-ignore -v` attributes the match to `.gitignore:1:.project-ai/`, and ground truth is protected. A text check for `.project-ai/transcripts/` finds no rule and would refuse a correctly configured repository.

**Claim C — "the rule, directory committed earlier → git tracks; undetectable by any text check."**
**CONFIRMED**, with an important mechanical correction (F4). Rule present, `a.vtt` force-added and committed:

```
git check-ignore -q            -- .project-ai/transcripts/   → exit 1   (NOT ignored)
git check-ignore -q --no-index -- .project-ai/transcripts/   → exit 0   (ignored)
git ls-files                   -- .project-ai/transcripts/   → .project-ai/transcripts/a.vtt
```

A `.gitignore` rule indeed does not untrack an indexed path, and it is indeed invisible to text matching. But `check-ignore` **without** `--no-index` already reports it as not ignored — which is the correction in §1.3.

**Claim D — trailing slash is load-bearing: "for a path that does not yet exist git answers *not ignored* for `…/transcripts` and *ignored* for `…/transcripts/`."**
**CONFIRMED exactly.** Rule `.project-ai/transcripts/`, directory absent:

```
check-ignore -q -- .project-ai/transcripts    → exit 1   (not ignored)
check-ignore -q -- .project-ai/transcripts/   → exit 0   (ignored)
```

With the directory *present*, both spellings answer `0`. So the asymmetry appears precisely on the first-capture-write path the AD identifies, and its conclusion — that the naive spelling refuses every correctly configured repository until someone creates the directory by hand — is right. This is the claim I was most prepared to find fabricated. It is not. See F13 for the one imprecision.

### 1.2 The prescribed spelling is sound — 10-configuration ground-truth matrix

Slash-appended spelling (what `GitVcs._as_git_path` produces), verdict vs. whether git actually stages a file inside:

| `.gitignore` | dir exists | `check-ignore -q` on `…/transcripts/` | ground truth |
|---|---|---|---|
| `.project-ai/transcripts/` | no | 0 ignored | protected ✓ |
| `.project-ai/transcripts/` | yes | 0 ignored | protected ✓ |
| rule + `!/.project-ai/transcripts/` | no | 1 not-ignored | **would commit** ✓ |
| rule + `!/.project-ai/transcripts/` | yes | 1 not-ignored | **would commit** ✓ |
| `.project-ai/` (parent) | no | 0 ignored | protected ✓ |
| `.project-ai/` (parent) | yes | 0 ignored | protected ✓ |
| (no rule) | no | 1 not-ignored | **would commit** ✓ |
| (no rule) | yes | 1 not-ignored | **would commit** ✓ |
| `.project-ai/transcripts` (no slash in rule) | no | 0 ignored | protected ✓ |
| `.project-ai/` + `!/.project-ai/` | no | 1 not-ignored | **would commit** ✓ |

**Ten for ten, and independent of whether the directory exists.** The adapter's comment — "Resolved without `create` … *and git answers the same either way*" — is true, but **only because** `_as_git_path` appends the slash first. Worth saying so in the code: as written the sentence reads as a general property of git, and §1.1 claim D is the proof that it is not.

### 1.3 F4 — two facts vs. one: the design is right, the stated reason is not

AD-43: *"The verdict carries two independent facts, `ignored` and `tracked`, never one: they call for two different repairs, and collapsing them to 'ignored' silently restores the third failure above."*

**The second half is false as written.** `git check-ignore` consults the index by default, so a tracked path answers *not ignored*, and a single-fact guard **refuses the write** — fail-closed, correct outcome, wrong diagnosis. Nothing is restored.

What *would* restore failure C is `--no-index`, and the AD never mentions the flag. The implemented adapter's docstring is better than the AD it implements:

> `git check-ignore` — Deliberately *without* `--no-index`: the default consults the index, so a path already tracked is correctly reported as not ignored. `--no-index` answers a different question … and answering that one would let a capture directory that is already committed read as protected.

**Why this matters rather than being pedantry:** the spine is the substrate a rebuild is written from. A reader who tests the AD's claim, finds it false, and concludes the second fact is redundant will collapse it — losing the *repair instruction*, which is what genuinely needs two facts. And there is one configuration where `tracked` is not redundant even for the verdict:

**Partial tracking.** Rule present, one file force-added, one not:

```
check-ignore -q  .project-ai/transcripts/            → 1  (not ignored)
check-ignore -q  .project-ai/transcripts/ok.vtt      → 0  (ignored)
check-ignore -q  .project-ai/transcripts/leaked.vtt  → 1  (not ignored)
ls-files         .project-ai/transcripts/            → leaked.vtt
```

`tracked` is what *names the leaked file*, and `TrackingVerdict.is_excluded = ignored and not tracked` is the correct conjunction.

**Recommend:** replace the AD's justification with "the two facts are two different repairs, and `check-ignore` alone cannot name the tracked file", and add the sentence the code already has — *`--no-index` answers a different question and must not be used.*

### 1.4 F5 — four configurations the AD's table does not cover

The AD presents three cases as the verified set. Four more are real, and two publish a transcript:

| # | Configuration | `check-ignore` | ground truth | in the AD? |
|---|---|---|---|---|
| 1 | **Nested `.gitignore`**: root excludes `.project-ai/transcripts/`, and `.project-ai/.gitignore` contains `!transcripts/` | 1 not-ignored | **GIT WOULD COMMIT IT** | ✗ — the table assumes the negation is in the root file |
| 2 | **`-v` on a negated match** (below) | **exit 0** while the path is **not** ignored | would commit | ✗ |
| 3 | `.git/info/exclude` carries the rule, repo `.gitignore` **empty** | 0 ignored | protected | ✗ — a text check refuses a valid repo |
| 4 | `core.excludesFile` carries the rule, repo `.gitignore` empty | 0 ignored | protected | ✗ — same |

Cases 3 and 4 *strengthen* AD-43 — two more ways a text matcher is wrong — and belong in the table as evidence. Case 1 is a genuine hole in the repair instruction (F14).

Case 2 is the sharpest:

```
# .gitignore: ".project-ai/transcripts/" then "!/.project-ai/transcripts/"
git check-ignore -q  -- .project-ai/transcripts   → exit 1
git check-ignore -v  -- .project-ai/transcripts   → exit 0
    .gitignore:2:!/.project-ai/transcripts/   .project-ai/transcripts
```

With `-v`, **exit 0 means "a pattern matched", including a negated one** — so a `-v`-based guard reports *ignored* on precisely AD-43's first failure row. `git-check-ignore(1)` documents the trap ("if the pattern begins with `!` then it is a negated pattern and matching it means the path is NOT excluded") while its EXIT STATUS section still says "0: One or more of the provided paths is ignored" — the man page contradicts itself, and the naive reading is the dangerous one.

The implemented adapter uses `--quiet` and is safe. **But AD-43 does not pin the flags**, and `-v` is exactly the flag a builder reaches for to satisfy the AD's own emphasis on naming the offending rule.

**Recommend:** AD-43 should name the invocation — `check-ignore --quiet` (never `-v` for the verdict, never `--no-index`), `ls-files -z`.

### 1.5 Error-path exit codes — the refusal is correctly shaped

AD-43: *"Any inability to consult it (not a repository, binary absent, unexpected exit, timeout) refuses the write."* Verified:

| Situation | `check-ignore` | `ls-files` |
|---|---|---|
| outside a repository | **128** | **128** |
| path outside the worktree | **128** | **128** |
| no path argument | **128** | — |
| nonexistent path inside the repo | 1 / 0 per rules | **0**, empty output |

`GitVcs` maps `check-ignore` ∈ {0, 1} to a verdict and **everything else** to `VcsUnavailable`, and `ls-files` ≠ 0 to `VcsUnavailable`. The right shape: 128 cannot be mistaken for "not ignored". Note the asymmetry — `ls-files` exits **0** for a nonexistent path, so an empty result is legitimately "not tracked", and the adapter is correct to treat only a nonzero code as unavailable.

**One residual worth stating in the AD:** `git ls-files` scoped to a path answers about the *current index*. A path staged and then `git rm --cached`'d reads as untracked even though an earlier commit contains it. AD-23's harm is about *future* commits, so this is the right question — but it is a question, not an identity, and the AD should say which one it asks.

---

## 2. AD-44 — no currency exposure, and internally consistent

The four-scope tree model (`File` / `Dir` / `Collection`, tiers on the node, sets derived rather than maintained alongside) leans on nothing external and so cannot go stale. Two spot-checks:

- `pm_ai/domain/scope_model.py` exists and is substantial (600+ lines) — this describes code, not a plan.
- AD-3's amended closing sentence ("the sets are derived from the scope model (AD-44), so an artifact cannot enter one without a declaration to derive it from") is consistent with AD-44's derivation claim for `ARTIFACT_TIER`, `BACKUP_TARGETS`, `REBUILD_TARGETS`, `RETENTION_MANAGED`, `DIAGNOSTIC_ONLY`.

AD-44's "the layout that preceded this declared 14 artifacts against 34 leaves named in that companion" is not independently verifiable from here — flagged as an unfalsifiable self-report, not an error.

**No currency finding against AD-44.** It is the one amendment with no external dependency to age.

---

## 3. F1 (HIGH) — `anthropic`: the one row that must not float is the one that floats

Spine: *"`anthropic` (Python SDK) | **pin exactly** (`0.124.0` current), `[mcp]` extra | **Never float**: `tool_runner` is a **beta** surface and AD-16 makes it load-bearing for the execution firewall."*

PyPI JSON API, 2026-08-22 — nothing yanked:

| version | uploaded |
|---|---|
| 0.123.0 | 2026-08-19 13:32 |
| **0.124.0** | 2026-08-19 16:51 ← the spine's "current" |
| 0.125.0 | 2026-08-19 22:00 |
| **1.0.0** | **2026-08-20 19:58** |

`0.124.0` was superseded **five hours later on the day it was verified**, and a **major** landed two days before this revision's `updated:` date.

And `pyproject.toml`:

```toml
runtime = [
    "anthropic[mcp]",              # ← no specifier at all
    "python-telegram-bot==22.8",
    ...
]
```

`uv.lock` currently resolves `anthropic 0.124.0`, so today's builds are reproducible — but the *declared* constraint is "any version", and the next `uv lock --upgrade` or `uv add` jumps to `1.0.0`. The document's most emphatic instruction is satisfied only by the lockfile, incidentally.

### What `1.0.0` actually contains

Verified against the SDK's `CHANGELOG.md` and `MIGRATION.md` at tag `v1.0.0`:

**The good news, and it is genuinely good for AD-16:**
- **`tool_runner` did NOT graduate and did NOT move.** Still `client.beta.messages.tool_runner` — `helpers.md` at v1.0.0 line 165 uses exactly that path. There is no `client.messages.tool_runner`. **AD-16's cited path survives the major version verbatim**, and the spine's characterisation of it as a standing beta risk remains accurate rather than alarmist.
- **The `[mcp]` extra still exists** — `helpers.md` v1.0.0 still documents `pip install anthropic[mcp]`; PyPI lists extras `aiohttp, aws, bedrock, google-cloud, mcp, vertex, webhooks`.

**The breaking changes that touch this spine:**
- **HTTP layer moved `httpx` → `httpx2`.** Type annotations and helpers change (`anthropic.Timeout` replaces `httpx.Timeout`). This directly affects the Stack row's own note — *"The extra pulls a second web stack including its own HTTP client; budget for it"* — because the base SDK's client is now `httpx2` while MCP's stack still carries `httpx`. The note is still true; the specific duplication it warns about has changed shape and is worth restating rather than inheriting.
- **`temperature` / `top_p` / `top_k` removed** from the message methods entirely (see F12).
- **`output_format` → `output_config`.** The spine already says `output_config.format`, so it is *ahead* of the pin it declares — correct for 1.x, and worth noting as an inconsistency between the prose and the pinned version.
- Minimum Python 3.9 → 3.10 (irrelevant here: `requires-python >=3.13`).
- Text Completions removed; async `.with_raw_response` must be awaited. Neither is used.

**Mitigating, and it is the argument for acting now:** `pm_ai/models/frontier/__init__.py` is empty and nothing in `pm_ai/` imports `anthropic`. The migration is free today and expensive once the adapter exists.

**Recommend:** add the specifier to `pyproject.toml`; decide 0.124.x vs 1.0.x deliberately (1.0.x, on this evidence — `tool_runner` is unchanged and the `output_config` prose already assumes it); and change the Stack row from a parenthetical "(`0.124.0` current)" to a dated pin, so the next reader can see at a glance whether it has aged.

---

## 4. F3 (HIGH) — AD-1 class L's constraints on the git adapter are enforced nowhere

AD-1 class L asserts four constraints, and AD-43 relies on them by reference ("the adapter in `pm_ai.platform` per AD-1 class L"):

> Allowlisted absolute binary path, argv list, `shell=False`, bounded timeout. **No model output may be interpolated into an argv.**

Against `pm_ai/platform/vcs.py`:

| Constraint | `GitVcs` | Enforced by a check? |
|---|---|---|
| argv list | ✅ `[git, "-C", str(repository), *arguments]` | ❌ |
| `shell=False` | ✅ (default, and commented) | ❌ |
| bounded timeout | ✅ `GIT_TIMEOUT_SECONDS = 10` | ❌ |
| **allowlisted absolute binary path** | ❌ **`shutil.which("git")`** | ❌ |
| no model output in argv | ✅ by construction today | ❌ |

**(a) The binary is not allowlisted.** `shutil.which("git")` resolves against the daemon's inherited `$PATH`. AD-1 specifies an *allowlisted absolute path* precisely so the spawned binary is not a function of the ambient environment. `pm_ai/platform/vcs.py` is the first code to exercise class L and it implements three of four constraints. The threat delta is small on a single-user macOS box — a `$PATH` attacker has more direct routes — but class L exists to confine a *capability*, and "whatever `git` resolves first" is wider than the AD grants. Either resolve and validate an absolute path once at construction, or amend AD-1 to permit `PATH` resolution for read-only queries. As it stands the document asserts a property the code does not have.

**(b) `pm_ai.platform` is exempt from the shell scan — the same gap the spine records as closed.** From `tests/architecture/test_static_rules.py`:

```python
SHELL_ALLOWED = {"platform"}
...
def test_ad1_no_shell_execution_outside_platform():
    layers = ["app", "domain", "core", "ports", "connectors", "skills", "surfaces", "storage"]
    ...
    for f, node, name in calls(source_files("models")):
        ...  # models.local: subprocess allowed, but shell=True is flagged
```

`platform` appears in neither the scanned `layers` list nor the `models` special case. Inside `pm_ai.platform`, `subprocess.run(..., shell=True)`, `os.system(...)`, `eval`, and `exec` would all pass. `pm_ai.models.local` at least gets an explicit `shell=True` check; the package that actually shells out gets none.

This is the *identical shape* of the bug the spine records as fixed:

> **AD-1 shell confinement** — `pm_ai.app` was in neither the AST scan's layer list nor `.importlinter`'s `subprocess-confined` contract — the composition root, the one layer permitted to import everything, was the one layer unscanned.

The remedy then (scan the exempt layer with a narrower carve-out) was not applied when `platform` became the second class-L home three days later. The spine's own standing rule — *"a check that cannot be shown to fail on a planted violation is not a check"* — has no coverage here, and its **Phase 1 exit criterion** ("zero skips … and every active check demonstrated to fail on a planted violation") will not catch it: there is no skip, and no check.

**Recommend:** give `platform` the `models.local`-style carve-out — `subprocess.*` permitted, `shell=True` / `os.system` / `os.popen` / `eval` / `exec` flagged — plus an assertion that the spawned binary path is absolute. Then plant a violation and watch it go red.

---

## 5. Anthropic API notes — one wrong, the rest precisely right

All checked against `platform.claude.com` docs, 2026-08-22.

### F2 (HIGH) — server-side `fallbacks` does not exist on Sonnet 5

Spine: *"**Both hazards below apply to Sonnet 5 as well as Opus 5.** An earlier revision scoped them to Opus 5, which exempted exactly the highest-volume paths — briefings, drafts, and inquiry synthesis all run Sonnet 5 under AD-15. … The router checks `stop_reason` before reading content, and **should opt into server-side `fallbacks`** so a decline is re-served rather than surfacing as a failed briefing."*

The *hazard* extension is correct and well-reasoned (see below). The *remedy* is not available:

> [Refusals and fallback](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback) — the feature applies to **Claude Fable 5 and Claude Opus 5**. Simplest form `fallbacks: "default"` with beta header `server-side-fallback-2026-07-01`; the API retries the declined request on Anthropic's recommended fallback for that refusal category, within the same call. Related: `fallback_credit`, so the cache cost is not paid twice.

Sonnet 5 is not in the supported set. So on the exact path the sentence is written for — briefings, per AD-15 — the router cannot opt into `fallbacks`, and a refusal must be handled client-side (retry, re-prompt, or surface the decline honestly).

**This is instructive in the same way R4's F1 was.** The revision correctly widened a hazard after review, then widened the mitigation with it by symmetry rather than by checking. The result is worse than the error it fixed: a builder reading this implements a server-side option that returns an error on Sonnet 5, and discovers it on the highest-volume path.

**Recommend:** split the sentence. The `stop_reason` check applies to both models and is mandatory. `fallbacks` applies to Opus 5 (coaching, research) and Fable 5 only; **Sonnet 5 needs a client-side refusal path**, which is a real piece of router design this document currently outsources to a flag that will not accept it.

### F12 (LOW) — "`temperature` / `top_p` / `top_k` are rejected (400)" needs one word

Live docs: *"On Claude Fable 5, Claude Mythos 5, Claude Mythos Preview, Claude Opus 5, Claude Opus 4.8, Claude Opus 4.7, and Claude Sonnet 5, **non-default** `temperature`, `top_p`, or `top_k` values return a 400 error on every request, regardless of whether thinking is used."*

Default values are accepted. As written the spine implies passing the parameter at all is an error. Minor, but it is the kind of sentence a builder writes a wrapper against. And under SDK `1.0.0` the parameters are **removed from the message methods entirely**, so on the 1.x path the question does not arise — worth noting alongside F1.

### Everything else in this section is confirmed, several of them exactly

| Claim | Verdict | Evidence |
|---|---|---|
| Thinking **on by default across the Claude 5 family — Opus 5 and Sonnet 5 alike** | **CONFIRMED** | "On Claude Opus 5, Claude Sonnet 5, Claude Fable 5 … thinking is already on: no configuration needed." Sonnet 5 can turn it off; Opus 5 accepts `thinking:{type:"disabled"}` only at effort ≤ `high` (`xhigh`/`max` + disabled = 400) — a detail the spine could add, since `effort` is in its own note |
| `max_tokens` caps thinking **plus** response text together | **CONFIRMED** | "the tokens Claude spends reasoning are billed as output tokens … and they count toward `max_tokens` alongside the response text." The spine's warning that "a route sized tightly around its answer will truncate" is exactly right |
| Assistant prefill rejected; use `output_config.format` | **CONFIRMED** | 400 with "This model does not support assistant message prefill" on Opus 5 / Sonnet 5 and the 4.6+ family. `output_format` is deprecated in favour of `output_config.format` |
| `output_config.effort` controls depth | **CONFIRMED** | `low`/`medium`/`high`/`xhigh`/`max`; **defaults to `high`** on both Opus 5 and Sonnet 5. Note for the router: effort is rendered into the prompt, so changing it **invalidates the prompt cache** — which interacts directly with the caching claim below and is unstated |
| `stop_reason: "refusal"` at HTTP 200, empty/partial content, `stop_details` category | **CONFIRMED** | Full set: `end_turn`, `max_tokens`, `stop_sequence`, `tool_use`, `pause_turn`, `refusal`, `model_context_window_exceeded`. `stop_details` populated only on refusal. The spine's "code that reads `content[0]` unconditionally breaks" is correct |
| **Prompt-cache minimums: 512 on Opus 5, 1024 on Sonnet 5** | **CONFIRMED EXACTLY** | Documented tiers: 512 → Opus 5 / Fable 5 / Mythos 5; **1,024 → Sonnet 5** (with Opus 4.8, Sonnet 4.6/4.5, Opus 4.1/4, Sonnet 4); 2,048 → Opus 4.7, Mythos Preview, Haiku 3.5; 4,096 → Opus 4.6/4.5, Haiku 4.5. Both numbers right, and the spine's inference — *briefings run Sonnet 5, so the persona/rules prefix must clear the higher floor* — is the correct operational consequence |
| Model ids `claude-opus-5`, `claude-sonnet-5` | **CONFIRMED** | These are the exact API ids. From the 4.6 generation on, ids "use a dateless format that is also a pinned snapshot, not an evergreen pointer" — so the bare string *is* the pin. **Worth stating in the spine**, because AD-16's "never float" instinct would otherwise push someone to hunt for a `-2026xxxx` suffix that does not exist |
| Cost model: Sonnet 5 **$2/$10**, permanent; increase to $3/$15 cancelled; Opus 5 **$5/$25** | **CONFIRMED** | Pricing page, verbatim: "The $2/$10 … announced at launch as introductory pricing through August 31, 2026, is now the standard price. The previously scheduled increase to $3/$15 … on September 1, 2026 will not occur." Opus 5 $5/$25 confirmed. **The R4 retraction was correct and the current text is right.** One caveat: the docs page carries no date, so the spine's specific "2026-08-10" is corroborated only by third-party reporting — either soften to "made permanent (announced August 2026)" or cite the secondary source |

`client.beta.messages.tool_runner` being a **beta** SDK surface: **CONFIRMED and unchanged through 1.0.0** (§3). The spine's framing — accepted deliberately, standing dependency risk, not an oversight — is the right one and is now backed by a major release that left it in `beta`.

---

## 6. Registry and vendor rows

### F6 (MEDIUM) — the preamble's verification date predates the revision

Re-check, 2026-08-22 (PyPI JSON API; none yanked):

| Row | Spine | Latest | Verdict |
|---|---|---|---|
| `anthropic` | `0.124.0` | **1.0.0** (2026-08-20) | **STALE** — F1 |
| `python-telegram-bot` | `22.8` | 22.8 (2026-06-12) | current |
| `fastapi` | `0.141.1` | 0.141.1 (2026-07-29) | current |
| `uvicorn` | `0.52.4` | 0.52.4 (2026-08-19) | current in the **table**; `pyproject`/`uv.lock` still pin **0.52.3** |
| `keyring` | `25.7.0` | 25.7.0 (2025-11-16) | current |
| `sqlcipher3` | `0.6.2` | 0.6.2 (2026-01-07) | current |
| `sqlite-vec` | `==0.1.9` | 0.1.9 stable (2026-03-31); `0.1.10a4` (2026-05-18) | current and correctly pinned |
| APScheduler (fallback) | `3.11.3` | 3.11.3 (2026-06-28) | current |
| `ollama` client | "pin at build time" | 0.6.2 (2026-04-29) | not a pin; `pyproject` has bare `"ollama"` |
| whisper.cpp | `v1.9.x` | **v1.9.3** (2026-08-20) | **current** — pin the patch |

**`uvicorn` drift is small but diagnostic.** R4 flagged `0.52.3` as stale on 2026-08-19; the *table* was corrected to `0.52.4`, and `pyproject.toml` was not. The spine and the build config now disagree on a row the spine claims alignment with — the drift mechanism, in miniature.

**`sqlite-vec` is the row where the spine's pessimism checks out precisely.** "Pre-1.0 … single-maintainer, last commit 2026-05-18, `0.1.10` alpha since April": the repo is **not archived** (8,040 stars, 202 open issues) but has had **no commit since 2026-05-18**, and `0.1.10` exists only as `alpha.1`–`alpha.4`. Latest stable remains `v0.1.9`. The README still warns "pre-v1, so expect breaking changes". Correctly characterised, correctly pinned exactly, correctly named a standing risk. ("since April" is a day out — `0.1.10a1` is 2026-03-31 — not worth changing.)

**`sqlcipher3` is correct as written**, and better than the note implies. `sqlcipher3-binary` 0.6.0 (2025-12-31) ships **7 wheels, all `manylinux2014_x86_64`** — no macOS, no arm64 — and targets SQLCipher **3.x**, lagging the main package's 4.x. Meanwhile `sqlcipher3` 0.6.2 ships **77 wheels including `macosx_11_0_arm64` and `universal2`**, self-contained. So the row's "**Not** `sqlcipher3-binary`, which publishes Linux-x86_64 wheels only" is confirmed, and the spine could add the useful positive: **no Homebrew sqlcipher is required on macOS arm64.**

### F8 (MEDIUM) — the Python row is stale twice over

Stack: *"Python | 3.13 (3.14 is the upgrade path)"*; `pyproject`: `requires-python = ">=3.13"`.

- **3.14 has been the stable line since 2025-10-07** — ten months. Latest is **3.14.7 (2026-08-05)**.
- **The project's own venv is 3.14.7**, and **282 of 293** `.pyc` files in the tree are `cpython-314`. The upgrade path has been taken.
- **3.13 drops to security-only on 2026-10-01** — about six weeks out — when **3.15 ships** (currently prerelease, scheduled 2026-10-01).

R4 called this row "conservative". It is now contradicted by the repository it describes, and the line it names goes security-only next month. Low consequence today (`>=3.13` admits 3.14), but a Stack table whose Python row is behind its own venv is one a reader stops trusting — and "3.14 is the upgrade path" should now read "3.15 (2026-10-01) is the next step; 3.13 is security-only from that date."

### F9 (MEDIUM) — the local-model candidates are aging, and the `llama3.3` number is wrong

Stack: *"Verified candidates: `llama3.1:8b` (4.9 GB), `qwen3:8b`. **Not** `llama3.3` — it ships 70B only"*; Deferred: *"Anything above 8B-class is out: the smallest `llama3.3` build is 26 GB."*

- **`llama3.1:8b` at 4.9 GB — CONFIRMED exactly**, 128K context, and the bare `:8b` tag *is* `Q4_K_M`. This is a precisely-verified number and deserves credit.
- **`qwen3:8b` — exists, 5.2 GB** (the spine gives no size), 40K context. The library lists the tag as "updated 1 year ago".
- **`llama3.3` 70B-only — shape CONFIRMED, number WRONG.** No 8B is published. But `q4_K_M` is **43 GB**; 26 GB is `70b-instruct-q2_K`. The spine's conclusion is *strengthened* by the correction — but "the smallest `llama3.3` build is 26 GB" is a specific figure that does not correspond to the quantization the rest of the row is written in (`Q4_K_M`), which is the signature of a recalled number rather than a looked-up one. It sits in the Deferred section as the stated reason a whole class is out of scope.
- **The candidate list has aged past the current generation.** The obvious 2026 successors mostly have no 8B: `llama4` is MoE-only (scout 17B×16E ≈ 67 GB); `qwen3.5` runs 0.8b/2b/4b/**9b**/27b/… (no 8b, 256K context); `gemma4`'s smallest local is 12b. The genuine current 8B-class instruct models are **`granite4.1:8b`** (`q4_K_M` = 5.3 GB, 128K ctx) and **`lfm2.5`** at 8B. Since Deferred says "Phase 1 benchmarks the verified candidates … and picks", Phase 1 would benchmark two aging tags and never see the current generation.

**Recommend:** correct 43 GB (or say `q2_K`), and add `granite4.1:8b` / `qwen3.5:9b` to the Phase 1 candidate set. The *class* framing ("8B-class instruct at `Q4_K_M`") is right and should stay; only the roster is stale.

### F15 (LOW) and the Ollama residency claim

`OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, and the request-level `keep_alive` field are all **current API** — confirmed against `docs.ollama.com/faq`. The residency argument in AD-19 and in the Stack prerequisite is sound: a client-side semaphore does not unload a model.

But the two variables are not equivalent, and the spine treats them as one instruction in three places:

- `OLLAMA_MAX_LOADED_MODELS` **defaults to 3** (3 × GPUs, or 3 on CPU) — so setting it to 1 is a **real fix**.
- `OLLAMA_NUM_PARALLEL` **already defaults to 1** — so setting it to 1 is **redundant**, exactly as R4's F9 said, and still unfixed. Worth keeping as a defensive assertion, but it should not be presented as a discovered prerequisite; it dilutes the one that matters.

### whisper.cpp — current, and R4's F10 now resolves to confirmed

- **`v1.9.x` is current, not stale.** Latest tag **v1.9.3 (2026-08-20)**; v1.9.2 2026-08-04, v1.9.1 2026-06-19, v1.9.0 2026-06-17. Recommend pinning the patch.
- **Metal on by default on Apple Silicon — CONFIRMED at source**, which R4 could only partially confirm from the README: `ggml/CMakeLists.txt` contains `if (APPLE) set(GGML_METAL_DEFAULT ON)` with `option(GGML_METAL … ${GGML_METAL_DEFAULT})`. The README adds "On Apple Silicon, the inference runs fully on the GPU via Metal." The spine's wording is right.
- **Core ML still build-time only — CONFIRMED**: `-DWHISPER_COREML=1`, plus `ane_transformers` / `openai-whisper` / `coremltools`, plus `./models/generate-coreml-model.sh`, and the README still says **"Python 3.11 is recommended"** — matching the spine's "separate Python 3.11 toolchain" claim precisely. Deferring it is well-founded.

### python-telegram-bot — F10 (MEDIUM), worse than R4 recorded

`22.8` is current (2026-06-12; nothing newer). `run_polling()` still seizes the loop — the docs say verbatim: *"When combining `python-telegram-bot` with other asyncio based frameworks, using this method is likely not the best choice, as it blocks the event loop until it receives a stop signal."* The prohibition is correct, and `[job-queue]` does still embed APScheduler (`APScheduler>=3.10.4,<3.12.0`), so that row is right too.

But the prescribed sequence is wrong in two ways. Spine: *"the bridge uses the manual lifecycle — `initialize()` → `start()` → `start_polling()`, with matching shutdown."* The documented order is:

```
initialize() → post_init() → Updater.start_polling() → start() → … → Updater.stop() → stop() → shutdown()
```

- **Wrong owner:** `start_polling` is on `Application.updater`, not on `Application`. R4 said this; it is unfixed.
- **Wrong order:** the docs put `Updater.start_polling()` **before** `Application.start()`, not after. R4 did not catch this.

A builder following the spine literally calls a method that does not exist on the object named, in an order the vendor inverts. Since AD-19's single-loop invariant rests on this sequence, it is worth getting exactly right in the one place the spine writes it down.

### F16 (INFO) — `sqlite-vec`'s risk is real and has no in-SQLite contingency

The spine names the supply risk and pins exactly, which is right. What it does not say is that there is **nowhere to fall back to inside SQLite**, which changes the shape of the contingency from "bump the pin" to "swap the store":

- **`sqlite-vss` is dead** — last push 2024-05-05, and its own author names `sqlite-vec` as the successor.
- **DuckDB VSS is officially experimental** — HNSW persistence requires `hnsw_enable_experimental_persistence=true`, with documented WAL-recovery data-loss/corruption caveats and full index re-serialization on every checkpoint. Not safe for persisted vectors, whatever DuckDB's own health.
- **LanceDB** (0.37.1, 2026-08-10) and **faiss-cpu** (1.15.0, 2026-08-03) are healthy with macOS arm64 wheels, but both are *separate stores*, not SQLite extensions — so adopting either means AD-3's Tier-3 `vector_index/` becomes a different artifact and AD-6's "unencrypted because rebuildable" argument needs re-checking against the new format.
- SQLite core has no first-party vector extension.

**Recommend:** one sentence in the risk noting that the fallback is a store swap with AD-3/AD-6 consequences, not a version bump. That is the difference between a risk that is monitored and one that is planned for.

---

## 7. F18 — `subprocess` vs. a library binding for git

The brief asks whether binding `subprocess`-invoked `git` is sound, or whether `pygit2`/libgit2, `dulwich`, or `GitPython` would be better. **`subprocess` is correct here, and for AD-43's purpose a library binding would be actively unsafe.** The evidence is specific, not stylistic.

**The decisive fact.** libgit2's own reference for `git_ignore_path_is_ignored` — the function behind `pygit2.Repository.path_is_ignored()` — states that it

> "indicates if the file would be ignored **regardless of whether the file is already in the index or committed**."

That is `--no-index` semantics, permanently, with no flag to turn it off. §1.1 claim C and §1.3 establish that the *only* reason a single-fact `check-ignore` guard fails closed on AD-43's third failure row is that the CLI consults the index by default. A `pygit2`-based guard would therefore report **ignored → protected** for a `transcripts/` directory that is already committed — reproducing AD-43's third failure exactly, the one the AD calls "undetectable by any text check", and reintroducing it via the tool chosen to replace the text check. The two-fact verdict would become mandatory rather than merely good practice, and its `tracked` half would have to come from `repo.index` anyway.

**Secondary arguments, all verified:**

- **AD-43's premise forbids a reimplementation.** The AD exists because a *reimplementation* of gitignore semantics disagrees with git in both directions. libgit2 is also a reimplementation — a far better one than a text matcher, but maintained separately and required to agree with the binary the user's own `git add` will run. AD-23's harm is defined by what *git* commits. libgit2 has documented gitignore divergences from the CLI ([#4610](https://github.com/libgit2/libgit2/issues/4610) — trailing-slash-plus-whitespace patterns not honoured; [#4295](https://github.com/libgit2/libgit2/issues/4295)) whose fix status in 1.9.x I could not confirm. And the **directory vs. trailing-slash behaviour for a nonexistent path — AD-43's load-bearing claim D — is entirely undocumented for libgit2.** Adopting it would mean re-deriving §1.2's ten-row matrix against a second implementation.
- **`GitPython` is not an alternative:** it shells out to `git` itself, so it is `subprocess` with a dependency attached — and it is in **maintenance mode** by its own README ("no feature development … no bug fixes, unless they are relevant to the safety of users"), despite shipping 3.1.59 on 2026-08-10.
- **`dulwich`** (1.2.12, 2026-07-19, healthy, pure-Python) is the same objection as libgit2 with less maturity behind its ignore handling.
- **Cost is two read-only invocations on a write path**, already bounded at 10s. Not a hot loop where spawn overhead argues for a binding.
- **A binding adds a native dependency.** `pygit2` 1.20.0 (2026-08-08) is healthy and wheels macOS arm64 with bundled libgit2 1.9.6 — but it pins to libgit2 **1.9.x** when built from source, and 1.20.0 itself removed the deprecated `pygit2.legacyenums` constants. That is another ABI-coupled dependency in a Stack already carrying `sqlcipher3` and `sqlite-vec` extension fragility.
- **AD-1 class L already confines the capability**, and `.importlinter`'s `subprocess-confined` contract counts *indirect* imports. A library binding would move the capability out from under that machinery into an ordinary import — arguably worse for AD-1's audit story.

**What the spine should add.** The undocumented cost of the subprocess choice is that the answer depends on the **ambient environment**, not only the repository:

- which `git` binary `$PATH` resolves (F3a) and **its version** — I verified on **Apple Git 2.50.1**, and AD-43 records no version at all behind "verified against real git";
- `core.excludesFile` and `.git/info/exclude`, which can make a directory protected with an entirely empty `.gitignore` (§1.4 cases 3–4). That is a *good* property — it is why git is the authority — but it means two byte-identical repositories can answer differently, and `pm-ai doctor` reporting *how* the directory is protected is worth more than a boolean.

**Verdict: keep `subprocess`, and say why.** The reason is not convenience; it is that the only implementation whose answer is definitionally correct is the one that will do the committing. That sentence belongs in AD-43, because it is also the argument that stops a future reader "modernising" it into `pygit2`.

---

## 8. Disposition of the R4 currency findings

| R4 | Then | Now |
|---|---|---|
| F1 HIGH — Sonnet 5 intro pricing expiry | Contradicted | **FIXED** — retracted explicitly, retraction recorded, re-verified correct (§5) |
| F2 MED — "every row re-verified against its registry" overstated | Overstated | **FIXED, then re-broken by time.** The preamble now separates registry-backed pins from pricing/API claims — a good structural fix — but the date is 2026-08-19 under a 2026-08-22 revision (F6) |
| F3 MED — refusal handling scoped to Opus 5 | Incomplete | **FIXED FOR THE HAZARD, BROKEN FOR THE REMEDY** — the extension to Sonnet 5 is right; `fallbacks` does not exist there (**F2, HIGH**) |
| F4 MED — thinking-on-by-default scoped to Opus 5 | Incomplete | **FIXED** — "on by default across the Claude 5 family", confirmed |
| F5 MED — four rows pinned as "latest" | Not a pin | **PARTIALLY FIXED.** `anthropic` now says "pin exactly" in prose and pins nothing in `pyproject` (F1). `uv`, `Ollama`, `ollama` client still "latest" / "pin at build time" |
| F6 MED — `sqlite-vec` risk understated | Understated | **FIXED** — now carries maintainer count, last-commit date, alpha status; all re-verified accurate. Contingency still unnamed (F16) |
| F7 LOW — `uvicorn 0.52.3` stale | Stale | **FIXED IN THE TABLE ONLY** — `pyproject`/`uv.lock` still 0.52.3 (F6) |
| F8 LOW — `start_polling` is on `Application.updater` | Imprecise | **NOT FIXED, and worse than recorded** — the order is inverted too (**F10**) |
| F9 LOW — `OLLAMA_NUM_PARALLEL=1` redundant | Redundant | **NOT FIXED** — still presented as required in three places (F15) |
| F10 LOW — whisper.cpp Metal default wording | Partially confirmed | **NOW FULLY CONFIRMED** at source (`GGML_METAL_DEFAULT ON`). The spine's wording is correct; no change needed |
| F11 INFO — Python 3.13/3.14 conservative | Conservative | **NOW CONTRADICTED BY THE REPO** — venv is 3.14.7, and 3.13 goes security-only in ~6 weeks (**F8**) |
| F12 INFO — embedding model unverifiable | Unverifiable | **UNCHANGED, and correctly so** — the Deferred entry states the pin-before-first-index constraint, which is the checkable part |

**Pattern worth naming.** Every R4 finding that lived only in *prose* was fixed. Every R4 finding that also lived in *`pyproject.toml`* was fixed in the prose and not the file (`uvicorn`, `anthropic`). And the one fix that required a *second* lookup rather than an edit — extending the refusal hazard to Sonnet 5 — was made by symmetry and got the remedy wrong. The spine is reviewed; the build config is not; and symmetry is not verification.

---

## 9. F17 — the `enable_load_extension` probe, confirmed on eight interpreters

Spine: *"`enable_load_extension` is **absent** on python.org and system CPython builds, **not merely disabled**. A uv-managed interpreter has it."* Probed with `hasattr(sqlite3.connect(':memory:'), 'enable_load_extension')`:

| Interpreter | Version | `enable_load_extension` |
|---|---|---|
| `/usr/bin/python3` (Xcode CLT) | 3.9.6 | **False** |
| `/usr/local/bin/python3` → python.org framework | 3.12.6 | **False** |
| python.org framework 3.12 | 3.12.6 | **False** |
| python.org framework 3.13 | 3.13.1 | **False** |
| python.org framework `Current` | 3.12.6 | **False** |
| uv-managed `cpython-3.13-macos-aarch64` | 3.13.9 | **True** |
| uv-managed `cpython-3.13-macos-x86_64` | 3.13.14 | **True** |
| **project `.venv`** (uv-managed) | **3.14.7** | **True** |

Exactly as stated, in both directions, across both the framework and Xcode builds — and `hasattr` is `False`, i.e. the attribute is genuinely **absent** rather than present-and-raising. That is the specific wording the spine chose, and the specific property the prescribed `pm-ai doctor` probe depends on. `[tool.uv] python-preference = "only-managed"` is present in `pyproject.toml`, so the guard exists in the build config as well as the prose.

Together with AD-43, this is the second decision in the document that is demonstrably first-hand.

**One note.** The runtime extra is not installed in the project venv (`pip list` is empty; the extras are deliberately opt-in per `pyproject`'s comment), so every Stack row except Python itself is **registry-verified, not import-verified**. `uv.lock` proves the set resolves together; nothing yet proves it imports and runs together. The `sqlite-vec` load path in particular — the one the spine calls out as "a start-up success followed by a total storage failure" — has not been executed. That is appropriate for Phase 0 and worth stating as such, because the Open Risks entry reads as though the failure mode has been reproduced.

---

## 10. Recommendations, in priority order

1. **Split the `fallbacks` sentence.** `stop_reason` checking applies to both models; server-side `fallbacks` is Opus 5 / Fable 5 only, and Sonnet 5 — the briefing path — needs a client-side refusal path the document currently does not specify (F2).
2. **Pin `anthropic` in `pyproject.toml`**, and decide 0.124.x vs 1.0.x deliberately while `pm_ai/models/frontier/` is still empty. `tool_runner` is unchanged in 1.0.0 and the spine's `output_config` prose already assumes 1.x (F1).
3. **Scan `pm_ai.platform` for shell calls** with the `models.local`-style carve-out, and plant a violation to prove it red. Resolve the git binary to a validated absolute path, or amend AD-1 to permit `PATH` resolution for read-only queries (F3).
4. **Restate AD-43's two-fact justification** on the ground that holds — two repairs, and `check-ignore` cannot name the tracked file — and name the invocation: `check-ignore --quiet`, never `-v` for the verdict, never `--no-index` (F4, F5 case 2).
5. **Add the four missing configurations** to AD-43's table, and record the git version behind "verified against real git" (F5, §7).
6. **Re-date the Stack preamble**, or say plainly that the registry rows were not re-verified on 2026-08-22; sync `uvicorn` to `0.52.4` in `pyproject.toml` (F6).
7. **Fix AD-42.6's "class L stays whisper.cpp alone"** and the matching stale comment above `.importlinter`'s `subprocess-confined` contract (F7).
8. **Update the Python row** to 3.14 with 3.15 (2026-10-01) as the next step and 3.13's security-only date noted (F8).
9. **Correct `llama3.3` to 43 GB at `q4_K_M`** and add `granite4.1:8b` / `qwen3.5:9b` to the Phase 1 candidate set (F9).
10. **Correct the Telegram lifecycle** to `initialize()` → `post_init()` → `Application.updater.start_polling()` → `start()`, with the documented reverse shutdown (F10).
11. **Mark which ports exist** in the layer table, and note that AD-12's `ModelPort` chokepoint is unbuilt (F11).
12. **Smaller edits:** "non-default" before `temperature`/`top_p`/`top_k` (F12); narrow AD-43's trailing-slash sentence to rules that carry a slash (F13); widen the guard's repair message to name the actual `.gitignore` file and line, which `check-ignore -v` supplies (F14); separate `OLLAMA_MAX_LOADED_MODELS` (a real fix) from `OLLAMA_NUM_PARALLEL` (already the default) (F15); note that `sqlite-vec`'s contingency is a store swap with AD-3/AD-6 consequences (F16); note that the bare `claude-opus-5` / `claude-sonnet-5` ids *are* pinned snapshots, so no date suffix should be hunted for (§5); pin whisper.cpp to `v1.9.3`; add that `sqlcipher3` 0.6.2 needs no Homebrew sqlcipher on macOS arm64 (§6).
