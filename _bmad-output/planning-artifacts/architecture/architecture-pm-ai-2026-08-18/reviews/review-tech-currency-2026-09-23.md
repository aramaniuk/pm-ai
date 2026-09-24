# Reviewer — technology currency and reality check (2026-09-23)

**Lens:** every committed decision must be web-researched or reality-checked, not asserted
from training data. Focus: the Stack table, every pinned version, model identifiers, and
claims about provider API behaviour.

**Scope note:** the spine was amended on 2026-09-23 to reconcile ADs, tables and diagrams
against shipped code. That amendment did **not** re-verify the Stack. The Stack preamble
still reads "re-verified against PyPI and `ollama.com` on 2026-08-19" — 35 days ago.

**Verdict: CONDITIONAL PASS.** No fabricated technology, no invented version, no wrong
model id, and — notably — the two highest-consequence non-registry claims (Sonnet 5
pricing and the per-model prompt-cache minimums) are **correct against live vendor
sources today**, including the retraction the spine already carries. Seven currency
findings, one of which (F2) is a direct spine-vs-lockfile contradiction now flagged by
three consecutive currency reviews, and one (F1) where the load-bearing pin is six minor
releases behind on a beta surface.

---

## What was checked, and how

Every row below was resolved live today (2026-09-23), not recalled. PyPI JSON API,
GitHub releases API, `platform.claude.com` pricing docs, `ollama.com` library pages, the
repo's own `pyproject.toml` / `uv.lock`, and a throwaway venv for SDK introspection.

### Registry rows

| Spine row | Spine says | `pyproject` / `uv.lock` | Upstream today | Verdict |
|---|---|---|---|---|
| anthropic | `==1.2.0` `[mcp]`, pinned 2026-08-28 | `==1.2.0`, lock 1.2.0 | **1.8.0** (2026-09-22) | **F1** — 6 minors behind |
| python-telegram-bot | 22.8 | `==22.8` | 22.8 (2026-06-12) | current |
| ollama (client) | "pin at build time" | unpinned, lock 0.6.2 | 0.6.2 (2026-04-29) | current; still unpinned (F3) |
| sqlite-vec | `==0.1.9` | `==0.1.9` | 0.1.9 (2026-03-31); last commit 2026-05-18 | **exact**, incl. the risk note |
| FastAPI | 0.141.1 | `==0.141.1` | 0.141.1 (2026-07-29) | current |
| uvicorn | **0.52.4** | **`==0.52.3`** | **0.53.0** (2026-09-14) | **F2** — contradicts lock *and* stale |
| keyring | 25.7.0 | `==25.7.0` | 25.7.0 (2025-11-16) | current |
| watchdog | `==6.0.0`, "latest release 2024-11-01" | `==6.0.0` | 6.0.0 (2024-11-01) | **exact**, incl. the standing-risk note |
| APScheduler (fallback) | 3.11.3 | n/a | 3.11.3 (2026-06-28) | current |
| whisper.cpp | v1.9.x | n/a | v1.9.4 (2026-09-11) | in range |
| Python | 3.13, 3.14 upgrade path | `>=3.13` | 3.14.7 GA; **3.15.0rc1** shipping | F5 (understated) |
| uv | "latest", install cmd | n/a | 0.12.18 (2026-09-22); local 0.12.2 | **F6** — command does not run |
| `git` CLI | "verified against 2.50.1 (Apple Git-155)" | n/a | this machine: **2.54.0 (Apple Git-157)** | F7 — claim no longer observed |
| — (no row) | — | `msal==1.38.0` | 1.39.0 (2026-09-17) | **F3** |
| — (no row) | — | `cryptography>=50.0.0`, lock 50.0.0 | 50.0.1 (2026-08-25) | **F3** |
| — (no row) | — | `tzdata` unpinned, lock **2026.3** | 2026.4 (2026-09-12) | **F3 / F4** |
| — (no row) | via `anthropic[mcp]` | lock `mcp==2.0.0`, `httpx2==2.12.0` | mcp 2.2.0; httpx2 2.13.1 | **F3** |

### Provider / model claims — measured, not recalled

| Spine claim | Result |
|---|---|
| Model ids `claude-opus-5`, `claude-sonnet-5` | **Both current and correct.** No date suffix, both live. |
| Sonnet 5 at **$2/$10** per MTok is the standard rate; the $3/$15 increase scheduled for 2026-09-01 **will not occur** | **Confirmed verbatim** on the live pricing page (model-pricing footnote 3). The spine's earlier retraction was right to be made. |
| Opus 5 remains $5/$25 | **Confirmed** on the live pricing page. |
| Prompt-cache minimum **512** on Opus 5, **1024** on Sonnet 5 | **Confirmed** against the current per-model minimums table. The spine's operational consequence (briefings run Sonnet 5 and must clear the higher floor) is right. |
| Thinking on by default across Opus 5 **and** Sonnet 5 | **Confirmed.** Omitting `thinking` runs adaptive on both. The earlier revision's Opus-only scoping was correctly widened. |
| `temperature` / `top_p` / `top_k` rejected (400) | **Confirmed**, and see below — also absent from the SDK at HEAD. |
| Assistant prefill rejected; use `output_config.format`; `output_config.effort` controls depth | **Confirmed.** |
| `stop_reason: "refusal"` arrives as HTTP 200 with `stop_details`; read `stop_reason` before `content[0]` | **Confirmed.** `stop_details` is populated *only* on refusal and is `null` otherwise — guard before reading it too. |
| `client.beta.messages.tool_runner` is a beta surface, absent from `client.messages` | **Confirmed by introspection at anthropic 1.8.0** (not just at the 1.2.0 pin): `beta.messages.tool_runner` present, `messages.tool_runner` absent. |

### Local-model rows — the rejection rationales still hold

Both re-fetched from `ollama.com` today: `llama3.1:8b` and `llama3.1:8b-instruct-q4_K_M`
are **4.9 GB**, as the spine states. `llama3.3` publishes **70B only** — 14 tags, all 70B,
smallest **26 GB** (`q2_K`) — which is exactly the spine's and Open Risks' claim, down to
the 26 GB floor.

### SDK reality check (throwaway venv, `anthropic[mcp]==1.8.0`, Python 3.13)

```
version 1.8.0
beta.tool_runner: True        messages.tool_runner (GA): False
sampling params present: []   mcp: 2.2.0 | httpx2: 2.13.1
```

Both properties the `==1.2.0` pin was justified by (beta-only `tool_runner`, no sampling
parameters) **still hold at 1.8.0**. That is the measured basis for F1 below.

---

## Findings

### F1 — HIGH. The one pin the spine says must never float is six minor releases behind, and the beta surface it protects has changed under it.

`anthropic==1.2.0` was pinned 2026-08-28, one day after 1.2.0 shipped — correct discipline
at the time. Since then: 1.3.0 (09-01), 1.4.0 (09-04), 1.5.0 (09-10), 1.6.0 (09-15),
1.7.0 (09-18), **1.8.0 (09-22)**. Six minors in 26 days on the SDK whose **beta**
`tool_runner` AD-16 makes load-bearing for the execution firewall. The upstream changelog
shows the runner itself moving — `compact_before_next_turn()` added and then reworked
across 1.6.0/1.7.0.

The finding is not "upgrade." The pin is defensible; the *evidence* for it is not current.
I measured 1.8.0 (above): `tool_runner` is still beta-only and the three sampling
parameters are still absent, so the two differentiators the row cites no longer
distinguish 1.2.0 from HEAD. What the pin now buys is six releases of unreviewed change on
a beta surface, held by a row that reads as freshly verified.

**Fix:** date the row's introspection evidence ("verified at 1.2.0, 2026-08-28"), and
state the review trigger — the pin is re-examined when the beta runner's API changes, not
on a calendar. Record that 1.8.0 was checked and holds the same two properties.

### F2 — MEDIUM-HIGH. The uvicorn row contradicts the lockfile, for the third review running, and is now stale on top of it.

Stack table: `uvicorn | 0.52.4`. `pyproject.toml` and `uv.lock`: `==0.52.3`. A spine claim
that contradicts the lockfile is a finding regardless of upstream — and here upstream has
moved again: **0.53.0**, published 2026-09-14.

This exact drift was raised in `review-r4-tech-currency.md` (F7, 2026-08-19) and again in
`review-r5-tech-currency.md` (F6 / stack appendix), which observed that the *table* was
corrected to 0.52.4 and `pyproject` was not. Nothing changed since. A row that has
survived two flagged reviews is no longer a stale-version problem; it is evidence that
Stack rows carrying a bare number and no constraint are not being maintained, which is
what R4 predicted in those words.

**Fix:** make the table match the lockfile (0.52.3), or bump both together. Either is
acceptable; disagreement is not.

### F3 — MEDIUM. Four runtime dependencies have no Stack row, and `pyproject` claims the table is the executable form of the Stack.

`pyproject.toml` opens the runtime extra with "The Stack table from ARCHITECTURE-SPINE.md,
made executable." It is not, in either direction:

- **`msal==1.38.0`** — no Stack row. It is the *only* thing that obtains a Microsoft
  token (AD-39, story 33a), pinned exactly and for a documented behavioural reason. Upstream
  is **1.39.0** (2026-09-17); the pin's two measured behaviours (`offline_access` refused as
  a user scope, `obtain_token_by_device_flow` owning the RFC 8628 polling loop) have not
  been re-checked against it. A credential path with a pin this deliberate belongs in the
  table.
- **`cryptography>=50.0.0`** — no Stack row, and it is AD-6's actual cipher. Floating
  minimum, lock resolved 50.0.0, upstream **50.0.1** (2026-08-25). It is also a dev
  dependency so the suite exercises the real cipher — a fact the spine never states.
- **`tzdata`** — no Stack row; see F4.
- **`mcp` (transitive, via `anthropic[mcp]`)** — the second web stack the anthropic row
  "budgets for." Lock has `mcp==2.0.0` / `httpx2==2.12.0`; upstream is **2.2.0**, and
  resolving `anthropic[mcp]==1.8.0` today pulls mcp 2.2.0 + httpx2 2.13.1. The spine warns
  about the second stack but never names what version of it is actually installed.

**Fix:** add rows for msal, cryptography and tzdata; record the resolved `mcp` / `httpx2`
versions in the anthropic row's note. Rows are how a decision stays checkable.

### F4 — MEDIUM. `tzdata`'s stated reason for being unpinned is defeated by the lockfile.

`pyproject` argues, at length and correctly, that the IANA database is *the* moving target
— "a country changing its DST rules is a release" — and that pinning it "would freeze this
machine's idea of when meetings happen." But `uv.lock` resolves `tzdata==2026.3`, and
upstream is **2026.4** (2026-09-12). A lockfile pins what a dependency range leaves open,
so in practice the entry *is* frozen, at a version that is now one DST-rules release
behind, and nothing in the repo says how it gets refreshed.

The exposure is narrow and correctly scoped by the existing comment — macOS supplies
system zoneinfo, so this bites only the slim-container path — but the comment currently
describes a property the build does not have.

**Fix:** either state the refresh mechanism (`uv lock --upgrade-package tzdata` on some
trigger, ideally a `pm-ai doctor` probe that reports the installed tzdata release), or
amend the comment to say the version is lock-frozen and refreshed deliberately. Do not
leave the rationale claiming currency the lock prevents.

### F5 — LOW-MEDIUM. The Stack preamble's verification date is 35 days old, and the Python row understates where CPython is.

The preamble asserts "re-verified against PyPI and `ollama.com` on 2026-08-19." Today's
amendment touched ADs, tables and diagrams around it without refreshing that line, which
is how F2 survived: a reader who trusts the banner re-checks nothing. This is the same
mechanism `review-r5-tech-currency.md` recorded as F6 ("stale by construction").

Separately: the row reads `Python | 3.13 (3.14 is the upgrade path)`. 3.14 is now GA and
well into its patch series (3.14.7 available as a uv-managed build), and **3.15.0rc1** is
already downloadable — `uv.lock` even carries `python_full_version >= '3.15'` resolution
markers. "Upgrade path" was written when 3.14 was prospective.

**Fix:** re-date the preamble with each amendment that touches the Stack, or say plainly
which rows the amendment did not re-verify. Restate the Python row against 3.14 GA.

### F6 — LOW. The uv row's install instruction is not a runnable command.

`| uv | latest | Install with `uv tool install --managed-python` |`. `uv tool install`
requires a `<PACKAGE>` argument (`Usage: uv tool install [OPTIONS] <PACKAGE>`); the command
as written installs nothing, and it is not how uv itself is installed. `--managed-python` is
a real flag (`UV_MANAGED_PYTHON`), and the *intent* — the interpreter must be uv-managed —
is already enforced correctly by `python-preference = "only-managed"` in `pyproject.toml`
and re-stated in the prerequisites. Only the command is wrong.

Also: "latest" is not a verifiable pin. Local uv is **0.12.2** (Homebrew, 2026-08-05);
upstream is **0.12.18** (2026-09-22).

**Fix:** replace with the real installer line and point the managed-python requirement at
`python-preference = "only-managed"`, which is where it is actually enforced.

### F7 — LOW. The `git` row's reality check names a version this machine no longer has.

The row says "verified against 2.50.1 (Apple Git-155)." This machine reports **git version
2.54.0 (Apple Git-157)**. The claim is not wrong — it records what was verified when — but
AD-43 makes `git` a hard runtime dependency of the capture write path, and the row reads as
a current statement. The behaviour it depends on (`git check-ignore`, `git ls-files`) is
stable across these versions, so consequence is low.

**Fix:** either drop the specific version (the row already says "any modern version") or
re-date it.

---

## Informational — not findings

- **A cheaper Opus now exists.** Claude Opus 5.5 is listed at **$4/$20** per MTok with
  cache reads at 0.05x ($0.20), against Opus 5's $5/$25 and 0.1x. AD-15 routes coaching and
  research to `claude-opus-5` and AD-17 anchors the $20/month target to Sonnet 5; neither is
  wrong, but the Opus tier now has a cheaper member with real behavioural differences
  (thinking cannot be disabled at any effort; forced `tool_choice` `any`/`tool` returns 400;
  default effort `medium`, not `high`). Worth a deliberate revisit rather than silent
  inheritance — and any such move is a migration, not a string swap.
- **The `fallbacks` opt-in is itself beta-gated.** The spine tells the router it "should opt
  into server-side `fallbacks`" without noting the parameter requires a beta flag
  (`server-side-fallback-2026-07-01` for the scalar `"default"` form; the older array form
  uses `-2026-06-01`, and pairing either header with the other form is a 400). That is the
  same class of dependency risk the spine is careful to flag for `tool_runner`, left
  unflagged here.
- **Prior reviews' verified negatives still hold.** `sqlcipher3-binary` remains
  Linux-x86_64-only in effect (the removal stands), `llama3.3` is still 70B-only, and
  `sqlite-vec`'s single-maintainer risk is stated with the correct dates. These are the rows
  that have aged best, because each records *why* a plausible alternative fails.

## Not verified in this pass

Stated so the next reviewer does not read silence as confirmation: whisper.cpp's Core ML
build-time claims (coremltools, the Python 3.11 toolchain, ANE first-run compile);
`keyring`'s `universal2` requirement; Ollama's `OLLAMA_MAX_LOADED_MODELS` /
`OLLAMA_NUM_PARALLEL` / `keep_alive` semantics; python-telegram-bot's `run_polling()`
loop-seizure claim; the Microsoft Graph tenant-admin and `calendarView` timezone claims;
and `sqlite-vec`'s `enable_load_extension` prerequisite (previously verified in-repo, not
re-run today). The embedding-model row remains an explicit Phase 1 decision, correctly
undated.

---

## Sources

- PyPI JSON API — `https://pypi.org/pypi/{anthropic,python-telegram-bot,ollama,sqlite-vec,fastapi,uvicorn,keyring,msal,cryptography,watchdog,tzdata,apscheduler,mcp,pytest,mypy,import-linter}/json` (retrieved 2026-09-23)
- `https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/CHANGELOG.md`
- GitHub releases/commits API — `ggml-org/whisper.cpp`, `asg017/sqlite-vec`, `astral-sh/uv`, `ollama/ollama`
- `https://platform.claude.com/docs/en/about-claude/pricing` (model pricing table + footnote 3)
- Current per-model prompt-cache minimums and Claude 5 request-surface rules (thinking defaults, removed sampling parameters, prefill, `stop_reason: "refusal"` / `stop_details`)
- `https://ollama.com/library/llama3.1/tags`, `https://ollama.com/library/llama3.3/tags`
- Repo: `pyproject.toml`, `uv.lock`, `git --version`, `uv tool install --help`, `uv python list`
- Throwaway venv: `anthropic[mcp]==1.8.0` on Python 3.13, introspected
