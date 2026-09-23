---
title: 'The filter measures itself'
type: 'feature'
created: '2026-09-20'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'b6b49b124effa3078dfac032bd40401a96fe7bf0'
context:
  - '{project-root}/_bmad-output/specs/spec-pm-ai/stories/8g-sanitization-normalises-before-matching.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `8g` asserts its fold against named cases, which proves those cases and nothing about the rule. Three questions stay unanswerable. *How often does it catch?* — there is no corpus, and the one pre-existing test feeds `sanitize()` the sentence its first pattern was written from, so the filter is measured against the input it was built to catch. *How often is it wrong?* — one false positive is already known, `"system: you are hitting the rate limit again"`, measured as matching both raw and folded, and nothing counts it. *Which rule cleaned a given text?* — the pattern set carries no version, so a change to the patterns, the carrier set, the fold or the Unicode data it depends on is invisible after the fact.

**Approach:** A versioned corpus with attack and benign halves, rates measured against pinned numbers, a fingerprint that fails when the rule moves without its version, and a committed benchmark that bounds the cost.

## Boundaries & Constraints

**Always:**
- **The corpus is pinned, or the rates are not comparable.** It carries its own version constant and a minimum count per half. A rate is recorded against a corpus version, and a commit that edits both corpus and rule states which direction the denominator moved — adding easy attack entries raises the catch rate without improving detection. A rate is never computed over an empty half.
- **Attack payloads are stored as escaped code points and decoded by the test**, with a self-check that at least one entry decodes to the carrier it claims. An invisible character survives neither a diff nor a whitespace-normalising hook, and an entry silently reverted to plain text makes the catch rate pass for the wrong reason — permanently, and without a failure anywhere.
- **Both rates are pinned to numbers, over sets this spec enumerates.** Catch: the eight attack cases `8g` names. False positives: those seven benign controls plus the known `system:` sentence. **Catch floor 8 of 8; false-positive ceiling 1 of 8, and the permitted hit is named.** A ceiling stated as a bare count would let a second false positive take the first one's place silently.
- **The direction of each ratchet is stated, because they move opposite ways.** A *fall* in the catch rate or a *rise* in the false-positive rate is a test failure, not a revised number in a report.
- **The known false positive is carried, not hidden and not fixed here.** `"system: you are hitting the rate limit again"` matches `system\s*:\s*you\s+are` raw and folded. Narrowing that pattern is a vocabulary change this slice forbids itself, so the entry sits in the benign half as the one permitted hit, visible in every run, and closing it belongs to whichever slice next owns the patterns.
- **Six examples do not close an axis, so composition is generated rather than enumerated.** A property test composes the declared transformations — insert a zero-width character or soft hyphen at any position, substitute full-width forms, vary separators, recase — over each corpus phrase, and every composition must be detected. `8g`'s eight cases are eight points; the claim they support is about a family.
- **The version is fingerprinted, not remembered.** A constant holds a hash over the pattern set, the carrier set, the fold's source and `unicodedata.unidata_version`, asserted against a pinned value, so any change — including a Python upgrade that moves NFKC beneath the code — reddens until the version constant and its changelog entry move together. `SCHEMA_VERSION` (`storage/service.py:145`) is the precedent for the shape and not for the discipline: that one is bumped by hand and this one cannot be.
- **The budget is stated per mix and measured through construction.** `8e`'s `__post_init__` re-runs the matcher on every `Sanitized`, so a call folds twice and `8g`'s match-only figures understate it. **2000 benign fields under two seconds; 2000 all-hit fields under five seconds.** The all-hit case is the attacker-controlled harvest this feature exists for — a budget measured only on benign fixtures measures the case nobody worried about. **The figures are pinned to the rule as built, with stated headroom:** measured 2026-09-23 through `sanitize()` on the machine the benchmark records, 2000 fields of 2.3 KB, a 200-call warmup and the median of five runs, benign took 1.26s and all-hit 3.26s, and each budget allows about 1.5x that. The first budgets, one and three seconds, were set before `8g` was built, against a design with one folded matcher. `8g`'s review then kept the literal matcher for the `system:` and delimiter families, which costs about 112µs of the roughly 325µs each benign pass takes. A call makes two passes, because `__post_init__` re-checks the value it was just handed.
- **The timed assertion states its protocol.** Warmup iterations, median of k runs, and the machine class recorded beside the figures, in a committed benchmark the test reuses. A bare wall-clock assertion against a 1.67x margin goes intermittently red on a shared runner and is marked `xfail` within a month.

**Ask First:**
- Whether the disclosure ledger records the version alongside the scopes and token counts AD-31 already requires. Recording it is the only way an audit can name *which* rule a given prompt passed, but it widens the Tier-1 grammar — which `8e` explicitly refused to do, because AD-27's entry grammar does not exist and `2c` withdrew `GRAMMAR_VERSION` as a constant written nowhere and read nowhere. **If this is unanswered at approval, the default is no: the version stays a module constant with no ledger field, and the audit question moves to whichever slice settles the entry grammar.**

**Never:**
- **No change to the fold, the patterns or the carrier set.** `8g` owns all three, and a slice that measures a rule may not also move it — including narrowing the `system:` pattern to retire the false positive it carries.
- **No new phrases, including chat-template markers.** The follow-on owns vocabulary.
- **No classification model.** It is the named next step for this feature, and these rates are the baseline it will have to beat.
- **No change to where sanitization is applied** (`8e`) **or which fields are untrusted** (`8c`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Catch rate at the floor | the eight attack cases | 8 of 8 reported and the assertion passes | N/A |
| Catch rate below the floor | one attack case regressed | failure naming every case that went undetected | assertion error |
| False positives at the ceiling | the eight benign entries | 1 of 8 reported, and the hit is the named `system:` entry | N/A |
| A different benign entry fires | the `system:` entry passes, another fails | failure — the count is met but the named entry is not the one that hit | assertion error |
| Composed transformations | generated compositions over each corpus phrase | every one detected; a failure prints the composition that escaped | assertion error |
| Corpus half empty or missing | corpus file absent, or one half has no entries | refused before any rate is computed | explicit error, never a division |
| Corpus below its minimum | fewer entries than the declared minimum per half | refused | explicit error |
| Payload silently de-escaped | an attack entry reverted to plain text | the self-check fails, naming the entry | assertion error |
| Rule changed without its version | patterns, carriers or fold source edited | the fingerprint no longer matches its pinned value | test failure until version and changelog move |
| Unicode data moves beneath the code | `unicodedata.unidata_version` changes | the fingerprint fails for the same reason | test failure |
| Budget, benign mix | 2000 benign fields of 2.3 KB | under two seconds, measured through `Sanitized()` | N/A |
| Budget, all-hit mix | 2000 matching fields of 2.3 KB | under five seconds, measured through `Sanitized()` | N/A |

</frozen-after-approval>

## Code Map

`8e` and `8g` are both merged (`b6b49b1`), so the anchors below are real. `8g` also left two readings this slice relies on: the composition property's "vary separators" means the separators the fold collapses (hyphen, dot, underscore, space, carriers), not punctuation next to whitespace, which is one of `8g`'s admitted evasions; and the rates are pinned over the enumerated sets only, while `8g`'s five admitted false positives, already pinned in its own tests, may sit in the corpus as reported, unpinned entries.

- `pm_ai/domain/sanitize.py` -- the fold, patterns and carrier set this slice fingerprints and measures; read-only here
- `pm_ai/storage/service.py:145,161` -- `SCHEMA_VERSION` / `UNVERSIONED`, the precedent for a versioned constant's shape
- `tests/domain/test_sanitization_fold.py` -- `8g`'s case-by-case assertions; this slice adds the rates over them rather than repeating them
- `tests/architecture/test_domain_invariants.py:141` -- the pre-existing test whose input is the sentence the first pattern was written from; the closed loop this slice opens
- `_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md:352` -- AD-31, what a model call must disclose, which the `Ask First` would extend

## Tasks & Acceptance

**Execution:**
- [x] `tests/domain/injection_corpus.toml` -- attack and benign halves, each entry carrying its source, payloads as escaped code points, a corpus version and a minimum count per half -- data, not code, so a reviewer can read what the rates were measured against
- [x] `pm_ai/domain/sanitize.py` -- the fingerprinted version constant and its changelog, and nothing else -- the rule itself is `8g`'s
- [x] `tests/domain/benchmark_sanitize.py` -- the committed benchmark the timed test reuses, recording machine and iteration count -- three figures carry the two-pass design and none is reproducible today
- [x] `tests/domain/test_sanitization_rates.py` -- the matrix: both rates against floor and ceiling, the composition property test, the corpus integrity checks, the fingerprint, and the two budgets

**Acceptance Criteria:**
- Given the corpus, then the catch rate is 8 of 8 and the false-positive rate is 1 of 8 with the hit identified as the `system:` entry — both failing loudly rather than reporting a smaller number.
- Given a generated composition of declared transformations over any corpus phrase, then it is detected, and a failure prints the composition that escaped.
- Given a corpus with an empty half, below its minimum, or holding a de-escaped payload, then the run refuses before computing a rate.
- Given any edit to the patterns, carrier set, fold source or `unicodedata.unidata_version`, then the fingerprint assertion fails until the version constant and a changelog entry move together.
- Given the committed benchmark, then 2000 benign fields complete under two seconds and 2000 all-hit fields under five, measured through `Sanitized()` construction as the median of k runs after a warmup, on the machine the benchmark records.
- Given `uv run pytest -q`, then `8g`'s fold suite still passes unchanged — this slice measures the rule and does not move it.

## Spec Change Log

- **2026-09-23, the budget re-pinned inside the frozen block, authorised by the human, before implementation.** Both budgets failed against `8g` as merged. Measured through `sanitize()` with the protocol this spec already requires, benign took 1.26s against one second and all-hit 3.26s against three. Profiling put the cost in the design, not in a slow spot: about 173µs of fold, 112µs of literal matcher and 23µs of carrier scan per pass, and two passes per call. The literal matcher is there because `8g`'s review kept the `system:` and delimiter families literal after folding them flagged ordinary prose. That came after this budget was written. Optimising the fold would have made this slice both move and measure the rule, which its `Never` exists to prevent. So the budgets were re-pinned to two and five seconds, about 1.5x headroom over the measured figures. Removing the second pass is left open: it is `8e`'s guard re-checking `sanitize()`'s own output, and changing that is a decision about the guard, not about measurement.

- **2026-09-20, split at drafting.** Carved out of `8g`, which carried the fold and its measurement together at 2,382 words. `8g` builds the fold and asserts named cases; this slice turns them into rates over a versioned corpus, fingerprints the rule, and bounds the cost. It depends on `8g` and not the reverse.

- **2026-09-20, amended against the multi-lens review of the pre-split draft.** Both thresholds became literals over enumerated sets after the review found the spec citing "a ceiling recorded in the spec" that it did not record. The ratchet direction was corrected — as written, a *rise* in the catch rate would have failed the build. The version became a fingerprint once the review showed the cited `SCHEMA_VERSION` precedent is bumped by discipline and no test could observe drift. The known `system:` false positive gained a disposition rather than staying cited-and-unresolved, the corpus gained a version and a minimum, payload escaping closed a silent-revert path, and the budget split into two mixes after the all-hit case measured 1.92s against what was then a single one-second budget.

## Design Notes

The named hit is the part worth defending. A ceiling of "at most one false positive" is satisfied by *any* one, so a second could appear as the first was fixed and the number would never move. Naming the permitted entry makes the ceiling a statement about which text is wrongly flagged, not merely how much.

The fingerprint exists because the precedent does not enforce itself. `SCHEMA_VERSION` is bumped by a human who remembers, in a repo where a migration makes forgetting loud. Nothing here is loud: a widened pattern set with a stale version produces correct-looking output and an audit trail that quietly lies about which rule ran.

## Verification

**Commands:**
- `uv run pytest tests/domain/test_sanitization_rates.py -q` -- expected: both rates print and meet floor and ceiling; the composition test passes
- `uv run python tests/domain/benchmark_sanitize.py` -- expected: both budget figures reported with machine and iteration count
- `uv run pytest -q` -- expected: no new failures

## Suggested Review Order

**Which rule ran**

- Entry point: the version, and the append-only changelog pinning each version's fingerprint and Unicode data.
  [`sanitize.py:744`](../../../../pm_ai/domain/sanitize.py#L744)
- What gets hashed: every name in the rule module, either hashed or exempted with a reason; foreign callables are flagged.
  [`test_sanitization_rates.py:1073`](../../../../tests/domain/test_sanitization_rates.py#L1073)
- A changed function body moves the fingerprint; asserted as a changed value, not a vanished key.
  [`test_sanitization_rates.py:1222`](../../../../tests/domain/test_sanitization_rates.py#L1222)

**The rates**

- Catch floor 8 of 8; a regression names every case it missed.
  [`test_sanitization_rates.py:579`](../../../../tests/domain/test_sanitization_rates.py#L579)
- Ceiling 1 of 8, and it must be the named `system:` entry, not merely one.
  [`test_sanitization_rates.py:593`](../../../../tests/domain/test_sanitization_rates.py#L593)
- The property: seeded compositions over each phrase, in four placements, bidi controls included.
  [`test_sanitization_rates.py:961`](../../../../tests/domain/test_sanitization_rates.py#L961)
- Misses found here, pinned as visible; red when a later slice starts catching them.
  [`test_sanitization_rates.py:847`](../../../../tests/domain/test_sanitization_rates.py#L847)

**The corpus**

- Data, not code: escaped payloads, exact claims per entry, a version and a minimum per half.
  [`injection_corpus.toml:37`](../../../../tests/domain/injection_corpus.toml#L37)
- Every malformed shape is refused by name, before any rate is computed.
  [`test_sanitization_rates.py:358`](../../../../tests/domain/test_sanitization_rates.py#L358)
- Loaded lazily, so a refusal fails the rate tests and leaves the fingerprint and budget tests running.
  [`test_sanitization_rates.py:424`](../../../../tests/domain/test_sanitization_rates.py#L424)

**The budget**

- Stops as soon as the verdict is settled; unit-tested against `statistics.median` with fixed run times.
  [`benchmark_sanitize.py:195`](../../../../tests/domain/benchmark_sanitize.py#L195)
- Recorded machine and figures, plus unbudgeted Cyrillic mixes: the budget bounds ASCII English only.
  [`benchmark_sanitize.py:72`](../../../../tests/domain/benchmark_sanitize.py#L72)
- The timed assertion, 2s benign and 5s all-hit, run through `Sanitized()` construction.
  [`test_sanitization_rates.py:1316`](../../../../tests/domain/test_sanitization_rates.py#L1316)
