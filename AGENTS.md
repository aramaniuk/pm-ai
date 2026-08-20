<!-- bmad:context -->
<!-- Verified 2026-08-20 against facf9f1. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## pm-ai

Local-first AI PM assistant — daemon, CLI, Telegram bridge, connectors, MCP skills. Python 3.13 under uv, hexagonal layering around a plugin kernel. The build contract is `_bmad-output/specs/spec-pm-ai/SPEC.md` plus the companions named in its frontmatter; architecture invariants AD-1…AD-42 live in `_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md`.

## Where things are

- What to build: `_bmad-output/specs/spec-pm-ai/SPEC.md` — CAP-1…CAP-40 and the constraints that bend every design decision.
- Work queue: `_bmad-output/specs/spec-pm-ai/stories.yaml` — 32 stories in execution order.
- Composition root is `pm_ai/app/`, the only layer permitted to import every other.
- Layering contracts in `.importlinter`; static invariant checks in `tests/architecture/`.

## Running and verifying

- Always `uv run <cmd>`. `pytest` is not on PATH, system Python is 3.12 against a `>=3.13` requirement, and `python-preference = "only-managed"` is set — sqlite-vec needs `enable_load_extension`, which is absent from stock macOS CPython.
- Run `uv run pytest` before claiming done — it is sub-second, and nothing else runs it: no CI, no pre-commit hook.
- Runtime deps are an extra, not defaults. `uv sync --extra runtime` before touching anything importing anthropic, fastapi, telegram, or sqlite-vec.
- `tests/architecture/test_layering.py` skips silently when `lint-imports` is missing, so a green run without it does not mean the import contracts hold.
- Invariant tests skip on modules that do not exist yet and name them; a story is done when its skip turns into a pass.

## Conventions that differ from defaults

- `_bmad-output/` artifacts are skill-derived: `SPEC.md`, its companions, and `.memlog.md` are re-rendered from the memlog, so a hand-edit is overwritten on the next derive. Change the contract by re-running the skill.

<!-- /bmad:context -->
