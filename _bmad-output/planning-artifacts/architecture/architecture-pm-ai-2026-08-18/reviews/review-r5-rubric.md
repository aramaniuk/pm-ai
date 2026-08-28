# R5 — Rubric Walk

- **Target:** `ARCHITECTURE-SPINE.md` (updated 2026-08-22 — adds AD-43, AD-44; amends AD-1 class L, AD-3's exclusion-set paragraph, the Design Paradigm ports row, two Consistency Conventions rows, the Tier-2 migration Deferred entry, the Capability map, the source tree)
- **Lens:** good-spine checklist — divergence coverage, rule enforceability, Deferred safety, tech currency, brownfield ratification, spec coverage, dimensional completeness
- **Date:** 2026-08-22
- **Evidence base:** the spine, `pm_ai/` (9,094 LOC), `tests/architecture/`, `.importlinter`, `_bmad-output/specs/spec-pm-ai/`, and a currency check on the Anthropic rows

## Verdict

**Changes requested.** The two new ADs are, on their own merits, the best-enforced invariants in the document — AD-43's rule is proven against real git in real repositories, and AD-44's is enforced by construction rather than by assertion. But the update landed with a structural insertion error that orphans three of AD-42's rules and leaves one of them flatly contradicting the amended AD-1; and the class L amendment widened the subprocess boundary into a package that no check scans. Neither is a design error. Both are the kind of error this spine has repeatedly caught in itself: a rule that reads as satisfied while an instance contradicts it.

Findings 1–5 are blocking. 6–14 should be fixed in the same pass; most are two-line edits.

---

## Blocking

### 1 — AD-42's rules 4b, 5 and 6 are orphaned under AD-44 (High)

AD-43 and AD-44 were inserted **inside AD-42's rule list**. AD-42 rule 4 ends at line 496; AD-43 opens at 498; AD-44 opens at 517; and at lines 533, 535, 537 the document resumes with `4b.`, `5.`, `6.` — AD-42's remaining rules, now rendered as if they belonged to the AD about directory trees.

Three consequences, all live:

- **AD-42 visibly ends at rule 4.** A builder reading the self-improvement AD stops before the Performance Index, before the append-only-memory rule, and before 42.6 — the strongest constraint in the AD.
- **The Deferred section cross-references "AD-42.6"** ("pm-ai names capability gaps in prose and a human writes the skill (AD-42.6)"). That reference no longer resolves to AD-42.
- **The Capability map** routes the whole self-improvement loop to `**AD-42**`, which as rendered no longer contains the prohibition on model-authored skill code.

Move lines 533–541 back above line 498.

### 2 — The orphaned 42.6 contradicts the amended AD-1 class L (High)

Line 537: *"class L stays whisper.cpp alone (AD-1)."*
Line 71 (amended, same document): class L is *"whisper.cpp; read-only local queries … `pm_ai.models.local` for whisper.cpp, `pm_ai.platform` for local queries."*

Two ADs, twenty-six lines apart, give opposite answers to "can the class L allowlist grow?" — and the one that says *no* is the one that also stipulates an "explicit **AD-1 amendment**" as the bar for widening it. That amendment just happened, and 42.6 was not updated to acknowledge it.

The fix is not to delete the clause; it is load-bearing against the self-improvement loop. Restate it as the property that actually holds: *class L admits no path on which model output reaches an argv.* That survives AD-43 and still forbids what 42.6 exists to forbid.

### 3 — Class L's new home is enforced by nothing (High)

The amendment moved a whole package inside the subprocess boundary. Enforcement did not follow.

`tests/architecture/test_static_rules.py:114` scans `["app", "domain", "core", "ports", "connectors", "skills", "surfaces", "storage"]`. **`platform` is absent.** The only other branch (`:122–131`) handles `models`, and it enforces `shell=False` for `models.local`. Net effect inside `pm_ai.platform`: `subprocess.run(shell=True)`, `os.system`, `os.popen`, `eval` and `exec` are all unchecked. `SHELL_ALLOWED = {"platform"}` is defined at line 17 and never referenced anywhere in `tests/` — a constant that reads as a constraint and is not one.

So class L's constraint column — "Allowlisted absolute binary path, argv list, `shell=False`, bounded timeout" — is partly enforced for the older half of the class and not at all for the newly-admitted half.

Compounding it, **the command set is not closed.** AD-1 names `git check-ignore` and `git ls-files` parenthetically, in prose. Everywhere else this spine closes an enumeration in `domain` and says why: event types (AD-27), verbs keyed on `(provider, verb)` (AD-32), push occasions (AD-40), reference kinds (AD-34). Under the amendment as written, a `git rm --cached` or `git commit` issued from `pm_ai.platform` violates no stated rule and trips no check. That is a mutation of external state — class M — reachable from the one package the spine just exempted.

Two fixes, both small: add `platform` to the AST scan (`shell=False`, no `os.system`/`eval`/`exec`), and close the permitted `(binary, subcommand)` set in `domain` the way AD-32 closes verbs.

Note also `.importlinter`'s `subprocess-confined` contract now carries `ignore_imports = pm_ai.app.wiring -> pm_ai.platform.vcs`. That edge is correct — the composition root must be able to construct the adapter — but it is a documented hole in an indirect-import contract and belongs in the Enforcement section's "still open" table rather than only in a config comment.

### 4 — AD-23 still describes the text check AD-43 proves cannot work (High)

AD-23: *"The daemon verifies the rule before writing a capture and refuses if it is absent."*

AD-43's own three-row table shows that a present rule can coexist with a tracked directory (negation line; committed-before-the-rule) and an absent rule can coexist with an excluded directory (parent exclude). "Verifies the rule … refuses if it is absent" is precisely the algorithm AD-43 was written to replace, and it gets two of the three cases wrong in the direction that publishes verbatim meeting minutes.

AD-23 was not in the amendment set. Neither was the storage diagram, whose `R4` node still reads *"daemon verifies the rule before writing."* AD-23 is the AD a builder reaches first, via FR-03/FR-08 and the transcript pipeline; AD-43 is downstream of it. Amend AD-23's sentence to point at git and at AD-43, and update the diagram node.

### 5 — AD-4 and AD-6 disagree on whether `people/` markdown is encrypted, and a ready-for-dev story is already forked on it (High)

- AD-6: *"**Only these are encrypted:** `operational.db`, `personal_analytics.db`, `transcripts/` …, `telegram_cache/`, and `config.json`"* — `people/` is absent — plus *"**All `.md` files in every scope stay plaintext by design**."*
- AD-4: `people/` is *"encrypted, gitignored, never committed."* The storage diagram labels `A8` "enc". `people/` holds career dossiers, which are markdown.

This is not new, but AD-44 explicitly **Binds AD-6**, and the update was the moment to resolve it. It did not, and the consequence is already downstream: `_bmad-output/specs/spec-pm-ai/stories/1e-encryption-classifier.md` is self-contradictory as written. Its **Never** clause: *"No Markdown file is ever classified as encrypted, in any scope."* Its own I/O matrix, line 43: `~/.pm-ai/private/people/p1/dossier.md` → **yes** → *"the one place a `.md` file is encrypted, because the enclave rule wins."*

A story that has to invent "the enclave rule wins" to reconcile two ADs is a divergence point the spine failed to fix. Decide it: either AD-6's plaintext promise is scoped to the three non-`people` scopes (and says so), or `people/` dossiers are plaintext inside an encrypted enclave (and AD-4 says *that*). Both are defensible. Silence is not, because two builders will pick differently and only one of them writes a report's performance record in the clear.

---

## Should fix in this pass

### 6 — AD-43 makes `git` a hard runtime dependency and the operational envelope never mentions it (Medium)

`git` appears nowhere in the **Stack** table, nowhere in the **Integration prerequisites**, and nowhere in the enumerated `pm-ai doctor` probes (keychain access, Ollama reachability, per-connector probe status, index and disk sizes, encryption-toggle state). Yet under AD-43 a missing `git` refuses every capture write — verified in `pm_ai/platform/vcs.py:_git`, which raises `VcsUnavailable("no `git` on PATH …")`.

AD-43's own closing sentence predicts the presentation: *"it presents as the feature not working rather than as a leak, which is why nothing would have caught it in production."* The spine then leaves that presentation undiagnosable. This is the same shape as the `sqlite-vec` / system-Python risk the spine already documents as an Open Risk with a `doctor` probe — and that one got a Stack row, a prerequisite, and an Open Risk entry. Give `git` the same three.

Related, same AD: `GitVcs` resolves the binary via `shutil.which("git")`. Class L's constraint says **allowlisted** absolute binary path. PATH-resolved is absolute but not allowlisted, and a `launchd` user agent's PATH is environment-derived. Either the constraint column loosens honestly for this member, or the path is pinned and probed.

### 7 — AD-43 puts a blocking 10-second subprocess inside the single writer, and no AD says where it runs (Medium)

`GitVcs._git` uses blocking `subprocess.run(..., timeout=GIT_TIMEOUT_SECONDS)` with `GIT_TIMEOUT_SECONDS = 10`. `StorageService` is fully synchronous (there is no `async def` anywhere in `pm_ai/` today). Every capture write into a project scope makes two such calls.

AD-19 grants the bounded worker pool to *"CPU/GPU-bound work (whisper.cpp transcription, embedding generation, Ollama inference)"* and states *"One asyncio event loop owns all I/O."* A blocking `git` on a cold index or a network filesystem stalls that loop for up to 10 seconds — AD-19's stated Prevents is *"a 30-second transcription making the Telegram bridge and CLI unresponsive"*, and 10s also blows through AD-21's 5-second acknowledge threshold on the ingestion path.

AD-43's **Binds** list omits AD-19 entirely. Either AD-43 states that the VCS query runs off the loop, or AD-19's carve-out widens from "CPU/GPU-bound" to "any blocking call with a bounded timeout". The second is probably the honest edit, since AD-43 is unlikely to be the last such call.

### 8 — AD-44 forbids maintaining the derived sets beside the trees; AD-3 and Deployment & operations both still do (Medium)

AD-44: *"`ARTIFACT_TIER`, `BACKUP_TARGETS`, `REBUILD_TARGETS`, `RETENTION_MANAGED` and `DIAGNOSTIC_ONLY` are **derived** from the trees, never maintained beside them."*

Two hand-maintained copies survive in the same document, both at artifact granularity:

- **AD-3's "Tier | Artifact | Rebuild target? | Backup target?" table.** Against the code's derived sets it names 6 of 25 artifacts. Absent: `people/`, `connectors/`, `skills/`, `telemetry/`, `config.json`, `projects.toml`, `config.toml`, `meetings/`, `rules/`, and the per-scope markdown leaves.
- **Deployment & operations → Backup.** This one is worse, because it is an instruction. It enumerates *"the markdown scopes …, `~/.pm-ai/disclosure.md`, `operational.db`, and `~/.manager-ai/private/personal_analytics.db`, plus an exported keychain key."* The code declares `people/` a Tier-1 backup target and `config.json` a Tier-2 one. Neither is named.

`people/` holding direct-report dossiers and being covered by no backup instruction is, structurally, the exact incident AD-3 memorializes about `personal_analytics.db`. And whether `people/` *should* be a backup target is genuinely open — AD-4 makes it "a single deletable directory" deleted on role change, which argues the other way. That is precisely why it must be decided in the spine rather than fall out of a derivation nobody reads.

Recommend: AD-3 keeps the tier *definitions* and drops the artifact column, pointing at `domain.scope_model` as the enumeration; the Backup bullet states the *policy* ("every `BACKUP_TARGETS` member, and here is why `people/` is or is not one") rather than a list.

### 9 — The tier vocabulary now labels code and config as "Truth" (Medium)

AD-44 makes `Tier` a **required** field on every declared node. That is the right call and it closes a real hole. It also forced a tier onto artifacts the tier vocabulary was never designed for: `skills/` (Python modules), `connectors/` (per-instance config files), `config.toml`, `projects.toml` and `people/` are all `Tier.TRUTH` in `APPLICATION_TREE`/`PERSONAL_TREE`.

AD-3's Tier-1 promise reads: *"Plaintext markdown, append-only, hand-editable, git-diffable. A backup target. Bounded by FR-37 compaction."* Of a `.py` file, a TOML registry, or an encrypted `people/` dossier, only "a backup target" is true. FR-37 compaction does not apply; append-only does not apply; git-diffable is false for `people/` by AD-4's own encryption. The code concedes the point in a comment — `telemetry/` *"is code rather than a record."*

The clean fix is one AD-3 already knows how to make: AD-6 says *"encryption tracks confidentiality; the tier tracks durability, and the two are independent."* Apply the same separation to format. Tier 1 promises **durability** (not derivable, backed up, never rebuilt); *plaintext-markdown-and-append-only* is a property of the ledger artifacts specifically, not of the tier.

### 10 — Retiring the Tier-2 migration deferral removed the deferral without creating the invariant (Medium)

Retiring it is correct — the precondition genuinely expired (`operational.db` exists; `_migrate` runs at construction), story `1i-operational-schema-versioning.md` is `ready-for-dev`, and the retirement note is the right kind of honesty about how the entry went stale.

But the concern's new home is a **strikethrough entry in the Deferred section** — the one section that by definition holds what is *not* decided. The rule-shaped sentence *"its schema changes need forward-only migration rather than drop-and-recreate"* is stated nowhere else. AD-3 says Tier 2 is never a rebuild target; it never says how Tier 2 changes shape.

Two units can still diverge on it:

- **`personal_analytics.db` is also Tier 2** (SQLCipher, `Tier.OPERATIONAL` in the code) and story 1i scopes itself to `operational.db` alone. The second Tier-2 store has no migration discipline in either the spine or a story.
- Today's `StorageService._migrate` is an ad-hoc `PRAGMA table_info` column-add with **no `schema_version` row**, so nothing yet refuses a store newer than the code — the failure mode story 1i names. Until 1i lands, the retirement claims settled ground that isn't.

Either fold the rule into AD-3 (one sentence: forward-only, ordered, never drop-and-recreate, a newer version refuses to open — for **every** Tier-2 store), or keep a live Deferred entry naming `personal_analytics.db`. A rule in a strikethrough is a rule nobody will find.

### 11 — AD-44 creates a migration dimension nothing owns: a Tier-1 *layout* change (Medium)

The layout is now code in `pm_ai.domain.scope_model`. So relocating or renaming a declared artifact is a code change with consequences on an installed machine — and it has already happened once: `meetings/` moved from the personal scope to per-scope on 2026-08-20 (AD-33 records it). Nothing in AD-44, AD-3, or Deferred says what becomes of files at the old path.

This is the checklist's Deferred test failing in the other direction. It is not deferred; it is absent. Two builders hitting the next layout change will resolve it differently — one migrating, one leaving orphans — and the orphan case silently drops Tier-1 truth. One Deferred entry ("Tier-1 layout migration — a declared artifact that moves needs a stated fate for files at the old path; unspecified until the first post-install move") would close it.

### 12 — The Enforcement section was not touched, so this update broke the spine's own rule (Medium)

Enforcement states: *"do not edit a check without editing its AD, and do not edit an AD without checking whether a test encodes it. An AD nothing enforces is a convention, and conventions drift."*

AD-43 and AD-44 arrived with two dedicated and genuinely strong suites — `tests/architecture/test_capture_guard.py` (17 tests, driven against real `git` in real temporary repositories, including the already-committed case no text check can see) and `tests/architecture/test_paths.py` (~45 tests covering the trees, the derived sets, ambiguity refusal, and `Collection` construction). Neither AD appears in the spine's Enforcement table nor in `tests/architecture/README.md`'s AD→check map (grepped: zero hits for `AD-43` or `AD-44`).

The table also still lists three mechanisms while the directory holds six files (`test_layering.py`, `test_capture_guard.py`, `test_paths.py`, `test_enforcement_meta.py` are all unlisted), and the README still asserts *"`pm_ai/platform/paths.py` is the only place a directory layout is written down"* — which AD-44 just made false.

Net effect: **the two best-enforced ADs in the document read as unenforced.** That is the inverse of the failure the Enforcement section was written about, and it is just as corrosive — it teaches a reader that the coverage table means nothing in either direction. The "Still open / coverage is overstated" audit is also stamped 2026-08-19 and was not re-measured against the two new suites.

### 13 — `TranscriptSourcePort` is in the amended ports row and does not exist (Medium)

`pm_ai/ports/__init__.py` declares `ConnectorPort`, `ScopePathPort`, `VcsPort`, `StoragePort`, `SkillPort`. The Design Paradigm row — amended in this pass — additionally lists `ModelPort`, `TranscriptSourcePort`, `KeychainPort`, `CryptoPort`, `SurfacePort`.

Most of those are honest Phase-1 forward references. `TranscriptSourcePort` is not, because **its adapters already exist and are already wired.** `GraphTranscriptAdapter` and `ManualTranscriptAdapter` are constructed in `app/wiring.py:96`, structurally typed against nothing. AD-23's chokepoint claim — *"All transcript ingestion goes through `TranscriptSourcePort`"* — is therefore currently unenforceable on a path that is built and running, which is a different situation from an unbuilt port.

Sharper still: `pm_ai.domain.transcripts.TranscriptSource` is a *different thing* with a near-identical name (an enum carrying the AD-32 trust property). That is the same one-word-two-meanings collision this spine flags three separate times — `SkillPermission` vs `DataScope` (AD-18), "derived" vs "Derived" (AD-3), tier vs horizon (AD-41). Since the ports row was amended anyway, mark which protocols are declared and which are Phase-1, and either rename the port or note the collision explicitly.

### 14 — `ScopePathPort` is not the contract its consumer uses (Medium)

`StorageService._assert_git_excludes` calls `self._paths.repository(scope.project_id)` (`pm_ai/storage/service.py:400`). `ScopePathPort` declares only `resolve` and `gitignore`.

So the port that AD-30 relies on — to let the single writer name its dependency without reaching across the `storage`/`platform` sibling boundary — is missing a method the writer requires. Any second implementation satisfies `@runtime_checkable` and then fails at the first capture write: a test double, or AD-26's promised Linux adapter. The port's docstring argues carefully about why `gitignore` belongs on it; `repository` got there without the same argument, on the same code path, in the same change.

Not a spine defect by itself, but it is a brownfield ratification miss on the exact seam this update touched, and the spine has no rule that a port declares everything its consumers call. Worth one line in the ports convention.

---

## Minor

- **The ports naming rule doesn't decide its own new example.** *"A **service-backed** adapter is named `<Service><Noun>Adapter`; an adapter with no service behind it is named for what it is (`ScopePaths`, `GitVcs`)."* `GitVcs` shells out to a binary — is a local binary a service? The forthcoming whisper and Ollama adapters hit the same question from the other side. Sharpen the discriminator to *remote* service, or to "holds credentials".
- **AD-44 binds no requirement.** Its Binds list is AD-3, AD-4, AD-6, AD-23, AD-38 and "every storage path" — no FR, NFR or UJ, unlike every other AD. NFR-07 and FR-16 are the natural anchors, and the frontmatter's `binds:` promises requirement coverage.
- **`pm_ai/connectors/gitlab.py:26–27`'s comment is now less honest than the spine.** It reads *"Injected (AD-30), never read from the ambient environment"* directly above `now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)`. The amended Clocks row admits what the code comment denies.
- **Currency (Anthropic rows):** verified — `claude-opus-5` and `claude-sonnet-5` are current model IDs, and `client.beta.messages.tool_runner` is still the beta surface AD-16 depends on. AD-15 and AD-16 hold. Two dated items the Stack's own caveat invites: (a) Sonnet 5 carries **introductory pricing that expires 2026-08-31** — ten days out — after which input/output move from $2/$10 to $3/$15 per MTok, stepping up AD-17's $20 monitored target and every cost estimate built on it; (b) the `anthropic` Python SDK has a documented **0.x → 1.x major transition** (httpx2, Python ≥ 3.10, removed deprecated parameters). The exact `0.124.0` pin with "never float" is the right call, but the eventual 1.x migration is named in no Deferred entry and no Open Risk, and AD-16 makes that SDK load-bearing for the execution firewall.

---

## Answers to the questions posed

**Do AD-43 and AD-44 carry Binds, Prevents and Rule, and are the Rules genuinely enforceable?**

Yes to both, and unusually so — these are the two most enforceable ADs in the document.

AD-43's rule is enforced by `pm_ai/platform/vcs.py` plus `tests/architecture/test_capture_guard.py`, which plants each of the three repository states from the AD's own table against real `git` and asserts refusal — including `test_an_already_tracked_capture_directory_refuses_the_capture` and `test_a_tracked_directory_is_refused_even_when_the_rules_exclude_it`, the case the AD says no text check can see. The two-facts verdict is real (`TrackingVerdict.ignored`, `.tracked`, with `is_excluded` computing the conjunction in one place so no caller re-derives it). Every unanswerable case raises `VcsUnavailable` and `test_any_unanswered_question_is_a_refusal` covers the refusal. The trailing-slash claim is implemented in `_as_git_path` with the reasoning at the call site.

AD-44's rule is enforced *by construction*, which is stronger than assertion: `File` cannot be built without a `Tier`, `Dir` cannot be empty, a `File` or `Dir` inside a `Collection` raises at construction, the five sets are computed from the trees, and `_assert_declarations_agree()` keeps them pairwise disjoint and keeps `REBUILD_TARGETS ∩ BACKUP_TARGETS` empty at import time. The `(scope, relative path)` identity claim is verified by `test_the_same_basename_in_two_scopes_is_two_different_declarations` and the ambiguity refusal by `test_an_ambiguous_basename_is_refused_rather_than_picked`.

The gaps are *around* these ADs — findings 3 and 12 — not in them.

**Does the amended class L weaken "the model's only route to an external effect is class M"?**

No — but not for the reason the AD gives, and the reason matters.

The invariant is about the *model*, and nothing model-derived reaches the argv. The single argument is a path returned by `ScopePaths.resolve()` over a closed artifact set (`GITIGNORE_REQUIRED`), in a scope whose `project_id` is validated as a directory name; class L already forbids interpolating model output into an argv; `shell=False` and an argv list hold. The invariant survives intact.

The AD defends it on the wrong axis, though. *"A read-only query is not egress"* argues about the **classification**, when the property actually holding the line is **who supplies the argv**. That distinction is worth making explicit, because the next candidate for class L will be argued from "it's read-only" — and read-only is not what keeps the model out. A read-only query whose argument came from a model prompt would satisfy the AD's stated justification and breach the invariant.

What *is* weakened is the enforcement, not the invariant: the newly-admitted home is scanned by nothing (finding 3), and the permitted command set is prose rather than a closed enumeration. The security property currently rests on the good judgement of whoever next edits `pm_ai/platform/`.

**Is admitting an outstanding exception in the Clocks convention right, or does it turn a rule into a suggestion?**

Right — and it is the best-calibrated edit in this pass.

Verified: `pm_ai/connectors/gitlab.py:28` carries the ambient default, and `pm_ai/app/wiring.py:132` always passes `now=clock`, so the exception is genuinely unreachable through the composition root. The claim in the table is accurate.

The alternative — leaving the blanket claim standing — is the documented failure mode this spine keeps catching in itself: AD-36's guard that read an attribute `NormalizedEvent` does not have, AD-38's vacuous write check, ten ADs with a populated "Enforced by" cell and no running check. A rule that reads as satisfied while an instance contradicts it is worse than an admitted exception, because it spends trust it does not have. The admission is narrow (one file, one field), names the remedy (`kw_only` on that dataclass), and states why it does not bite.

Two things stop it decaying into a suggestion, and only one of them is present:

- The exception has **no owner and no expiry**. "Outstanding" with a date is a debt; "outstanding" without one is a permanent second reading of the rule.
- **Nothing but `wiring.py` prevents a second exception.** No check asserts that no module outside `pm_ai.app` reads the ambient clock — this would be a trivial AST rule alongside the existing shell and write scans. Without it, a second ambient default arrives silently, and the table becomes as trustworthy as the pre-2026-08-19 coverage cells. *A convention with one admitted exception and no check is one commit from having two.*

Separately, the row's absolute phrasing — *"No component reads the ambient clock"* — technically indicts `app/wiring.py:80`, which reads it correctly, since the same row names the composition root as the source. "No component outside the composition root" is the exact statement.

**Do AD-43 or AD-44 contradict, weaken, or duplicate AD-1 through AD-42?**

| AD | Relation | Assessment |
| --- | --- | --- |
| **AD-1** | contradicted; enforcement weakened | Findings 2 and 3 |
| **AD-3** | partially duplicated | AD-3's artifact-granularity table is the hand-maintained copy AD-44 forbids, and it is now materially incomplete — finding 8. Tier vocabulary strain — finding 9 |
| **AD-4** | consistent, strengthened | AD-44 is the mechanism AD-4 always needed. `Collection` is the piece AD-4 lacked: it lets "the contents of `people/` cannot be enumerated" be a stated decision rather than an omission |
| **AD-5** | consistent, strengthened | `_writable_dir` funnels every directory write through the guard, so AD-43 cannot be bypassed by adding a write path that resolves for itself |
| **AD-6** | **contradicted** | `people/` encryption — finding 5 |
| **AD-19** | **unaddressed collision** | Blocking subprocess on the single loop — finding 7 |
| **AD-23** | **contradicted** | Finding 4 |
| **AD-26** | cosmetic friction | `GitVcs` is the first `pm_ai.platform` member that is *not* OS-specific — `git` is not a macOS API. AD-26's "OS-touching concerns behind ports" no longer quite describes what the package holds |
| **AD-27 / AD-33 / AD-34** | no overlap | AD-44's `(scope, relative path)` is *artifact* identity; AD-34's `(scope, source_system, source_ref)` is *event* identity. Disjoint keys, and neither is described as the other |
| **AD-30** | consistent, with a noted hole | AD-43 forced `.importlinter`'s `ignore_imports = pm_ai.app.wiring -> pm_ai.platform.vcs`. That is the composition root doing its job, but it is a hole in an indirect-import contract and belongs in Enforcement |
| **AD-38** | serves it | The capture guard is how AD-38's "never committed" becomes checkable for a directory rather than a record. No conflict |
| **AD-42** | **structurally broken; contradicted** | Findings 1 and 2 |

**Is retiring the Tier-2 migration Deferred entry correct, or does the concern still need a home?**

Retiring it is correct. The precondition genuinely expired — `operational.db` (SQLite, WAL) exists, `_migrate` runs at construction, and story 1i is `ready-for-dev` with a full I/O matrix. The retirement note's honesty about *how* the entry went stale ("three days before anyone noticed") is the right register.

But the concern still needs a home, and finding 10 gives the two reasons: `personal_analytics.db` is also Tier 2 and no story covers it, and the invariant itself now lives only as strikethrough prose inside the section reserved for undecided things. Retiring a deferral means the concern becomes an AD or becomes scoped work. Here it became scoped work for one of two stores, and the rule that would have covered both was left in the Deferred section.

---

## What holds

Worth recording, since a review that only lists problems misrepresents the document:

- **AD-43 is the strongest new invariant in this spine.** It identifies a guard that would have reported *protected* for a tracked directory, proves the failure against real git in three real repository states, refuses on every unanswerable case, and keeps the two facts separate because they call for two different repairs. The trailing-slash detail — the naive spelling refuses every *correctly configured* repository until someone creates the directory by hand — is the kind of thing that is only ever found by running it.
- **AD-44 replaces a flat table with a structure that can be diffed against its source document.** The `Collection` node is the load-bearing idea and the AD knows it: it converts absence from an omission into a declaration, which is what made "14 artifacts declared against 34 leaves in `scope-model.md`, and nobody could tell" detectable at all. Moving the tier onto the node makes the drift the old import-time assertions guarded *unrepresentable* rather than merely caught.
- The **`is_excluded` conjunction living in one place** so no caller re-derives it "in the direction that publishes a transcript" is exactly the reasoning AD-34 applies to join keys, correctly reused.
- The **Open Risks entry on recent AD truthfulness** ("On 2026-08-19 three were not, and each read as satisfied with a passing test") remains the most valuable paragraph in the document, and its standing lesson — *prefer a test that drives the real path over one that hands the answer in* — is what `test_capture_guard.py` actually does.

## Recommended order of fixes

1. Move lines 533–541 back above line 498 (finding 1). Purely mechanical; unblocks 2.
2. Amend 42.6's class L clause and AD-23's verification sentence plus the `R4` diagram node (findings 2, 4). Prose.
3. Decide `people/` encryption in AD-6 or AD-4, and note it on story 1e (finding 5).
4. Add `platform` to the AST shell scan; close the permitted `(binary, subcommand)` set in `domain` (finding 3).
5. Add `git` to Stack, Integration prerequisites, `doctor`, and Open Risks; state where the VCS call runs relative to AD-19 (findings 6, 7).
6. Add AD-43 and AD-44 to the Enforcement table and the README map; list the three unlisted test files; re-date the coverage audit (finding 12).
7. Reconcile AD-3's artifact table and the Backup bullet with the derived sets, deciding `people/` and `config.json` explicitly (finding 8); separate durability from format in Tier 1 (finding 9).
8. Give Tier-2 migration an AD sentence covering both stores, and open a Deferred entry for Tier-1 layout migration (findings 10, 11).
9. Mark which ports are declared vs Phase-1; note the `TranscriptSource`/`TranscriptSourcePort` collision; add `repository` to `ScopePathPort` (findings 13, 14).
