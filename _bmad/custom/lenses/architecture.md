# Architecture Lens (pm-ai)

Review the content against pm-ai's architecture invariants. The content is either a slice spec or a diff. Your question is not whether it is good code. Your question is which AD it touches, and whether it keeps that AD.

## Load first

- `{project-root}/_bmad-output/planning-artifacts/architecture/architecture-pm-ai-2026-08-18/ARCHITECTURE-SPINE.md`. Read the Design Paradigm, then every AD the content plausibly touches, including its revisions and amendments. The spine is the authority. Do not restate it from memory.
- `{project-root}/.importlinter` (layering contracts) and `{project-root}/tests/architecture/README.md` (which AD each check enforces, and which checks still skip).

## Method

1. List the ADs the content touches. For a diff, judge by the modules, ports, storage paths, egress points, scopes and processes it changes. For a spec, judge by its Boundaries, Code Map and Design Notes.
2. For each one, check the content keeps the invariant as the spine currently states it. Pay particular attention to:
   - layering and imports, where `pm_ai/app/` is the only layer that may import every other;
   - the single writer for persistent state, and append-only ledgers written only through storage append methods;
   - storage tiers and where each artifact lives (scope trees, the resolver; no component deriving its own path);
   - egress classes and the model boundary (sanitising every externally sourced field before a model sees it);
   - the daemon as the one long-lived process, and thin clients over authenticated loopback;
   - scheduling owned by the daemon, and connectors as pull adapters;
   - the one Proposal entity for staged-then-approved flows.
3. Flag a change that needs a spine amendment but does not record one. Flag an amendment that contradicts or weakens an existing AD instead of extending it.
4. Flag a new invariant the content relies on that no test in `tests/architecture/` enforces. Also flag a check the content should turn from skip to pass but does not.
5. Do not flag style, naming or behaviour that no AD covers. Leave those to the other lenses. Zero findings is a valid result.

## Findings shape

Use the canonical fields, and add `ad`, the AD id or ids the finding concerns:

- `location` is file:line for code, or the section for a spec.
- `trigger_condition` is the departure from the AD, in one line.
- `guard_snippet` is the concrete fix, the missing check, or the amendment needed.
- `potential_consequence` is what diverges if it ships as-is.

No severity, priority or ranking.
