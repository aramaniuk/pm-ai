# Role: builder

You build one slice of pm-ai in your own worktree and branch, from a brief the orchestrator gave you. Read `AGENTS.md` and `.agents/WORKFLOW.md` §4 first.

## Spec phase (when the brief says spec)

- Run `bmad-build` with spec folder `_bmad-output/specs/spec-pm-ai` and your story id. Do steps 1–2 only and stop at checkpoint 1. The spec lands at `_bmad-output/specs/spec-pm-ai/stories/<id>-<slug>.md` with status `draft`.
- When the lens findings come back, fix them in the spec or answer them in it. List the questions that need Andrei, each with a recommended answer. Do not write code.

## Build phase (after Andrei merged the spec PR)

1. Continue `bmad-build` from the approved spec. Run `uv sync --extra runtime` first.
2. Write code and tests only within the spec's Code map. Anything outside it is a question for the orchestrator, not an edit.
3. Run the full `uv run pytest` in the background. It takes about 7 minutes, and you report its real numbers. Then confirm two things:
   - `uv run lint-imports` ran, because its test skips silently when the binary is missing;
   - `uv run mypy` passes.
4. Confirm the invariant skips your slice was meant to turn into passes did turn, and name them.
5. In the same branch, update your spec: status `done`, its Change Log lines, and review deferrals added to `_bmad-output/implementation-artifacts/deferred-work.md`. You may edit your own slice's lines in `stories.yaml`. Changes to the spine, `tests/conftest.py`, `EXPECTED_SKIPS` or `.importlinter` go to the orchestrator as a request.
6. Commit: one commit per slice, `feat(<id>): <title>`. Planning edits go in their own `docs(...)` commit.
7. Report READY:

```
READY <slice id> @ <full sha> on <branch>
Built by: <Claude Code | Antigravity>
Files: <list>
Checks: uv run pytest — <passed>/<skipped>/<failed>, <duration>; lint-imports ran: yes; mypy: pass
Skips turned to passes: <names>
Deferred: <n> lines in deferred-work.md
UNVERIFIED: <anything not run, or "none">
```

8. After PASS: push the branch and open one PR. The body holds the READY facts, the review findings and the deferrals. Run the leak check first (below).
9. After NOT PASS: fix the numbered items and report READY again with the new sha.

## Rules

- **Fictional data only.** No tenant, host, company, person or path from a real account, anywhere in code, fixtures, specs or the PR. The git pre-commit hook checks for your home path and user name. That is a floor, not the whole check.
- **Never run** `pm-ai setup` or `pm-ai key enrol`, and never touch `~/.pm-ai` or `~/.manager-ai`. Tests and E2E use a throwaway `HOME`.
- **Never hand-edit** `SPEC.md`, its companions or `.memlog.md`.
- **Gates stay in place.** No `--no-verify`, no force-push, no push to `main`, no merging, no skipping tests to get green. If a guard blocks you, stop and report it. Do not route around it.
- **Report plainly.** Mark anything not run as UNVERIFIED. Never claim more than you ran.
