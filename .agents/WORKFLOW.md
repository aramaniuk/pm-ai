# pm-ai: orchestrator and agent team — workflow

Approved by Andrei 2026-10-27 (v2). Section 5 lists what is configured and how to turn it on in a new clone.

One owner decides (Andrei). One orchestrator session assigns, reviews and keeps the ledger, but does not write product code. Builder sessions build one slice each, in their own worktree and branch. Reviewer agents give PASS / NOT PASS on an exact commit. Andrei approves every spec before it is built and approves every merge.

The workflow is written to be tool-neutral: Claude Code runs every role today, and an Antigravity executor can be added later without changing the stages (section 4, "Second executor").

## 1. Components and where they live

| Component | What it does | Where |
| --- | --- | --- |
| Repo rules | Build contract, uv/Python 3.14 rules, test-suite timing, runtime extra, skill-derived artifacts. Read by Claude Code and by Antigravity | `AGENTS.md` (exists) |
| Contract | The capabilities (CAP-n — the set grows as the spec is updated), constraints, companions; the architecture invariants (AD-n) | `_bmad-output/specs/spec-pm-ai/SPEC.md` + companions; `ARCHITECTURE-SPINE.md` (exist) |
| Work queue | Stories in execution order, their slices, and `invoke_dev_with` notes from Andrei's decisions | `_bmad-output/specs/spec-pm-ai/stories.yaml` (exists) |
| Work order | One spec per slice: Intent, Boundaries, I/O matrix, Code map, Tasks & Acceptance, Change log, Verification. Status `draft` → `ready-for-dev` → `done` | `_bmad-output/specs/spec-pm-ai/stories/<id>-*.md` (exists) |
| Deferred work | Review findings consciously not fixed in the slice | `_bmad-output/implementation-artifacts/deferred-work.md` (exists) |
| Memory | Andrei's standing rules (approval gate, batch questions, no invented evidence, public repo, wave-end E2E, …) | `~/.claude/projects/<project>/memory/` (exists, per machine) |
| Orchestrator ledger | Who holds which slice, branch, worktree, sha under review, verdicts, merge approvals, open questions; one daily log | `~/.claude/orchestrator/pm-ai/ledger.md` + `daily-YYYY-MM-DD.md` — **outside the repo** (public repo) |
| Grants | Which worktree may edit, for which slice | `~/.claude/orchestrator/pm-ai/grants.json` |
| Guard scripts | The hard rules as plain scripts, called by Claude Code hooks and by local git hooks, so they hold for any executor | `scripts/guards/guards.py`, `scripts/git-hooks/` |
| Role briefs | One plain-markdown brief per role, tool-neutral; Claude agents are thin wrappers that load them | `.agents/roles/*.md` + `.claude/agents/*.md` |
| Skills | `bmad-build`, `bmad-review`, `bmad-code-review`, `bmad-architecture`, `bmad-spec`, `bmad-sprint-planning` (readiness), `bmad-retrospective`, `bmad-correct-course`; Claude Code's built-in `security-review` | `.claude/skills/` (exist) |
| Review lenses (new) | Two project lenses added to `bmad-review`: `architecture` and `privacy-scope` (section 2) | `_bmad/custom/bmad-review.toml` + `_bmad/custom/lenses/` |
| Watch | Desktop-app session notifications for Claude sessions (primary); a 20–30 min poll of PRs and the ledger (secondary, and the primary one for an Antigravity executor) | App + `/loop` in the orchestrator session |
| E2E | Recorded scenario replayed against a throwaway home with real Ollama qwen3:8b; findings per run | `e2e/scenario.md`, `e2e/test_wave_e2e.py`, `e2e/findings-YYYY-MM-DD.md` |

## 2. Roles

| Role | Who runs it | Uses | Does | Never does |
| --- | --- | --- | --- | --- |
| Owner (Andrei) | the person | — | Picks the wave; answers the batched questions; approves each slice split and each spec; approves each merge (merges himself, or tells the orchestrator to); enters every credential | — |
| Orchestrator | one Claude Code session in the main checkout, on `main` | `bmad-sprint-planning` (readiness), the ledger | Runs readiness and slicing; starts builder sessions with a brief; writes grants; reviews each READY at its sha; dispatches reviewers; posts PASS / NOT PASS; batches questions for Andrei; merges a PR only on Andrei's word for that PR; runs the wave-end E2E, fix round and spec sync | Writes product code or tests; edits a worktree it did not grant; merges without Andrei's word; approves a spec on his behalf; passes a diff it did not read; acts on another session's denied action |
| Spec writer | builder session | `bmad-build` steps 1–2 | Drafts the slice spec from its `stories.yaml` entry, the spine and the code | Writes code before the spec is approved |
| Spec reviewers | subagents | `bmad-review` lenses: adversarial, edge-case, verification-gap | Review the draft; findings are fixed or answered in the spec | Edit the spec themselves |
| Architecture steward | subagent | `bmad-review` with the new `architecture` lens (on specs and diffs); `bmad-architecture` (extend / pressure-test the spine when a slice needs an amendment) | Checks against the AD-n invariants and `.importlinter`; confirms `lint-imports` actually ran; drafts spine amendments | Changes the spine without Andrei's approval |
| Privacy & scope steward | subagent | `bmad-review` with the new `privacy-scope` lens; Claude Code `security-review` on the diff | Checks the scope model, encryption tiers, append-only ledgers, prompt-injection filter, keychain and connector tokens; runs the public-repo leak check on diff, fixtures and PR body | Fixes code silently |
| Builder | Claude Code session per slice, own worktree (Antigravity later) | `bmad-build` steps 3–5 | Builds against the approved spec; runs the full suite; updates the spec (status, change log, deferrals) in the same branch; posts READY | Push to `main`, merge, edit another slice's files, hand-edit skill-derived files, run `pm-ai setup` / `key enrol`, touch `~/.pm-ai` |
| Code reviewer | subagent | `bmad-code-review` | Adversarial review of the diff at the READY sha against the spec's acceptance criteria | Edit code |
| Verifier | subagent, cheaper model (Antigravity later) | — | Re-runs `uv run pytest` at the sha (background, ~7 min); confirms the slice's invariant skips turned into passes | Trusts a builder's "all green" without output |
| E2E runner | orchestrator at wave end | `uv run pytest e2e/ -v` | Runs the scenario against a throwaway home; writes the findings file; extends `e2e/scenario.md` with the wave's new steps | Uses real accounts or field data |

**Why `bmad-build` writes the spec.** It is BMad's one implementation workflow and it has two halves: steps 1–2 investigate the code and fill the slice spec template (Code map, I/O matrix, acceptance), then stop at its first checkpoint for approval; steps 3–5 build, review and present against that same file. Using its first half for spec writing keeps the spec in the exact shape its second half consumes, and the 116 done slices were written this way. `bmad-spec` is a different level: it owns `SPEC.md` and its companions (the whole-product contract) and is used for the wave-end spec sync, not for slice specs.

**Why new lenses rather than new skills.** Neither BMad nor superpowers ships an architecture-conformance or privacy reviewer for code. `bmad-review` accepts project lenses through `_bmad/custom/bmad-review.toml`, so the two stewards become two lens files that load the spine (or the scope model, storage contract and privacy constraints) as standing facts. They then run the same way as the three spec lenses, and the same lens file can be given to an Antigravity reviewer.

## 3. Stages: who does what

1. **Wave pick.** Andrei names the wave (or the next stories). The orchestrator lists the candidate stories from `stories.yaml` and their prerequisites.
2. **Readiness and slicing.** For each story: a readiness check against current `main` (`bmad-sprint-planning` readiness, as for 7a–7e), the open questions it raises, and a proposed split into slices (as story 15 became 15a–15d) — each slice one module or behaviour, one PR. Andrei's decisions and the split are recorded in that story's `stories.yaml` entry (`invoke_dev_with`) in a `docs/wave<N>-<id>-readiness` PR.
3. **Spec draft.** One builder session per slice drafts its spec (`bmad-build` steps 1–2, status `draft`). Slices may be drafted in parallel — each touches only its own spec file.
4. **Spec review.** Three lenses on each draft; the architecture and privacy-scope lenses where the slice touches their area. The writer fixes what they find.
5. **Questions to Andrei.** The orchestrator collects only the questions that change product behaviour, security, or depart from the plan — across all slices in flight — into one batch, with a recommended answer each. Routine choices are decided and stated.
6. **Approval: the gate.** Andrei approves each spec through its `docs/<id>-spec` PR. No code before this. Status `ready-for-dev`.
7. **Work order.** The orchestrator writes the grant (worktree ↔ slice), records it in the ledger, and starts the builder with: slice id, spec path, branch `claude/feat-<id>-<slug>`, the files it owns, and the rules in section 4. At most 2 builders at once.
8. **Build.** In its own worktree: code and tests against the spec; `uv sync --extra runtime`; full `uv run pytest` in the background; confirm `lint-imports` and mypy ran. The spec gets status `done`, its change log lines and deferrals in the same branch. Then a READY report (format below).
9. **Review.** The orchestrator reads the diff at that sha and dispatches the code reviewer, the stewards where relevant, and the verifier where the builder's evidence is incomplete. It posts PASS or NOT PASS with a numbered list. NOT PASS goes back to the same builder.
10. **PR.** On PASS the builder opens one PR per slice; the body carries the READY facts, the review findings and what was deferred. Leak check before posting.
11. **Merge.** Andrei approves each merge: he merges himself, or tells the orchestrator "merge #N", and it merges that PR (`gh pr merge --merge`), recording his words and the sha in the ledger. A finished slice does not wait for a go-ahead before the next independent slice starts (rule of 2026-10-09); a slice that depends on it waits for the merge.
12. **Wave E2E and fix round.** When the wave's slices are merged: run the E2E against a throwaway home and write `e2e/findings-YYYY-MM-DD.md`. Each finding is triaged — product bug, test or scenario out of date, or a spec/behaviour misalignment — and becomes a fix slice (`fix/<topic>`, same build → review → PR → merge path; a fix that changes behaviour goes to Andrei first). Test updates and scenario alignment land with the fixes. E2E is re-run until green or every remaining finding is accepted by Andrei and recorded in `deferred-work.md`.
13. **Upstream spec sync.** The wave's decisions and amendments are carried back up: `bmad-spec` (update intent) re-derives `SPEC.md` and its companions from the memlog — capabilities may be added or changed; `bmad-architecture` finalises the spine amendments; the PRD and solution design are synced (as in `docs/spec-sync-wave3`); `stories.yaml` is reconciled (slices done, new or deferred stories). One `docs/spec-sync-wave<N>` PR. Then a retrospective if Andrei wants one; the daily log summarises the wave.

**READY report** (PR comment and ledger line; the same text whichever tool built it):

```
READY <slice id> @ <full sha> on <branch>
Built by: <Claude Code | Antigravity>
Files: <list>
Checks: uv run pytest — <passed>/<skipped>/<failed>, <duration>; lint-imports ran: yes; mypy: pass
Skips turned to passes: <names>
Deferred: <n> lines in deferred-work.md
UNVERIFIED: <anything not run, or "none">
```

## 4. Rules that make it work

- **One writer per set of files.** Each builder has its own worktree and branch, and owns its slice spec and the code its Code map names. A builder may edit its own slice's lines in `stories.yaml` and add its own deferrals to `deferred-work.md`; amendments to `ARCHITECTURE-SPINE.md`, and shared test infrastructure (`tests/conftest.py`, `EXPECTED_SKIPS`, `.importlinter`), go through the orchestrator only.
- **Two slices run side by side only when** neither needs the other's result and their Code maps do not overlap. Otherwise they are sequenced.
- **Verdicts on an exact sha.** A READY without a sha is not reviewed. Silence is not PASS. No PASS without reading the diff.
- **Evidence, not claims.** Anything not run is marked UNVERIFIED. A green suite without `lint-imports` on PATH is not a pass for the import contracts.
- **The interpreter is fixed.** arm64 `uv`, Python 3.14. If the rule-fingerprint test fails, fix the interpreter; never record a new rule version.
- **Skill-derived files are not hand-edited.** `SPEC.md`, its companions and `.memlog.md` change only by re-running the skill.
- **Nothing touches the real home or keychain.** No `pm-ai setup`, no `key enrol`, nothing under `~/.pm-ai` or `~/.manager-ai`. E2E uses a throwaway `HOME` and `PM_AI_DISABLE_ENCRYPTION=1`.
- **Public repo.** No field data in code, fixtures, specs, PR bodies or comments; fictional data only; leak check before every push.
- **No gate is skipped.** No `--no-verify`, no force-push, no skipped tests to get green. If a guard blocks, stop and report.
- **Merges only on Andrei's word, per PR.** An approval of one PR does not extend to another.
- **No permission laundering.** If one session or tool is denied an action, no other session or tool does it for it; it goes to Andrei.
- **Stay in your lane.** Nobody reviews, fixes or merges another slice's work without an assignment.
- **Andrei's standing rules apply to every session and every tool:** no invented evidence; report plainly; never ask what the input already says; CLI is an API layer; plain language in specs.

**Second executor (Antigravity, later).** Not connected now. Prepared for by:
- every role brief and review lens is a plain markdown file Antigravity can load, with `AGENTS.md` as the shared entry point;
- the hard rules live in `scripts/guards/`, run by Claude Code hooks *and* by local git hooks (`pre-commit`, `pre-push`), so they hold for an executor that has no Claude hooks;
- briefs, READY reports and verdicts are text in the ledger and on the PR, not Claude-only session messages;
- Antigravity work is picked up by the polling watch (it does not send Claude session notifications), and the orchestrator reviews it at its sha exactly like Claude work;
- candidate first roles for Antigravity: the verifier and one builder lane. When connected, its exact rules/workflow files are checked against its current docs and added to `.agents/` — a separate slice.

## 5. What is configured

1. **Role briefs** in `.agents/roles/`: `orchestrator`, `builder`, `architecture-steward`, `privacy-scope-steward`, `code-reviewer`, `verifier`, `e2e-runner`; thin wrappers in `.claude/agents/` (verifier on a cheaper model; the others on the builders' model).
2. **Review lenses**: `architecture` and `privacy-scope` added to `bmad-review` through `_bmad/custom/bmad-review.toml`.
3. **Guards**: `scripts/guards/guards.py` (standard library only; tests in `tests/tooling/`), wired as Claude Code hooks in `.claude/settings.json` (PreToolUse on Edit/Write/Bash, Stop) and as git hooks in `scripts/git-hooks/` (pre-commit: grant + leak check on staged lines; pre-push: no push to main, no non-fast-forward):
   - `grant` — refuses edits outside a granted worktree (`~/.claude/orchestrator/pm-ai/grants.json`, shape `{"<worktree folder>": {"slice": "16a", "branch": "claude/feat-16a-…", "granted": "YYYY-MM-DD"}}`); the main checkout is granted only for docs and planning work.
   - `derived` — refuses hand-edits to `SPEC.md`, its companions and `.memlog.md`.
   - `home` — refuses `pm-ai setup`, `pm-ai key enrol`, and writes under `~/.pm-ai` / `~/.manager-ai`.
   - `gate` — refuses `--no-verify`, force-push, pushes to `main`.
   - `quick-check` (end of a builder turn) — `lint-imports` present + mypy on touched packages; the full 7-minute suite stays a deliberate background step.
4. **Orchestrator ledger** files under `~/.claude/orchestrator/pm-ai/`.
5. **Fallback watch**: `/loop` every 20–30 min in the orchestrator session over `gh pr list` and the ledger; "no change" when nothing changed.

**Turning it on in a clone.** `.claude/` is excluded locally (`.git/info/exclude`), so `.claude/settings.json` and `.claude/agents/` are tracked by force-add; the BMad skills under `.claude/skills/` are installed per machine. Then:

1. `git config core.hooksPath scripts/git-hooks`
2. create `~/.claude/orchestrator/pm-ai/grants.json` (`{}` to start) and `ledger.md`, both 0600
3. start the orchestrator session in the main checkout with: "You are the pm-ai orchestrator. Read `.agents/roles/orchestrator.md` and follow it."

With no grants file every edit in a pm-ai worktree is refused, Andrei's own sessions included: grant the main checkout (key = its folder name) for ad-hoc work, or remove the key when done.

Not to do: copy credentials or tokens into any brief, commit the ledger or grants, or give any executor access to Andrei's real home.

## 6. Decisions (Andrei, 2026-10-27)

| # | Decision |
| --- | --- |
| D1 | At most 2 builders at once |
| D2 | Specs approved through the `docs/<id>-spec` PR |
| D3 | A builder edits its own slice's lines in `stories.yaml` and its own deferrals; spine amendments and shared test infrastructure go through the orchestrator only |
| D4 | READY reports and verdicts in both PR comments (facts only, leak-checked) and the local ledger |
| D5 | No second executor now; Antigravity to be connected later (section 4) |
| D6 | Code reviewer and stewards on the builders' model; the verifier on a cheaper model; the verifier (and possibly reviewers) are candidates to move to Antigravity |
| D7 | The orchestrator starts the next independent slice on its own after a PASS, never one whose spec Andrei has not approved |
| D8 | Merge: Andrei merges, or approves a named PR and the orchestrator merges it |
