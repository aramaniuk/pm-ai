# Privacy & Scope Lens (pm-ai)

Review the content for ways it could move data across a scope boundary, weaken protection at rest, mishandle a secret, or leak real-account data into the public repository. The content is either a slice spec or a diff.

## Load first

- `{project-root}/_bmad-output/specs/spec-pm-ai/scope-model.md` and `{project-root}/_bmad-output/specs/spec-pm-ai/storage-contract.md`.
- The Constraints in `{project-root}/_bmad-output/specs/spec-pm-ai/SPEC.md`.
- In the spine, the ADs on scopes, storage tiers, encryption and the keychain, egress classes, and sanitising at the model boundary. Read their current text. Do not work from memory.

## Method

Check, where the content touches them:

1. **Scope.** Can data from one scope (personal, project, people/team-member, application) reach another scope's file, page, prompt, model call or delivery? Pay most attention to people-scope material reaching a project page, and to personal material reaching anything shared. Is the scope fixed by type, or only by convention?
2. **Tiers and encryption.** Does each new file have a declared tier, and the `encrypted` and `gitignored` settings its scope requires? Does anything write outside the resolver? Are permissions (0600 files, 0700 folders) kept for private files?
3. **Ledgers.** Is a new ledger registered as append-only and written only through the storage append method? Could a rewrite or truncation slip through?
4. **Model boundary.** Is every externally sourced field sanitised before it reaches a prompt? Is a frontier (non-local) call allowed by the egress class for that data, and recorded in the disclosure ledger?
5. **Secrets.** Do tokens, keys or the keychain appear in logs, errors, `--json` output, test fixtures or exception messages? Does anything except `setup` or `key enrol` write the keychain?
6. **Public repo.** Does the content include real tenant, client or user ids, host names, company, team, channel or person names, message text from a real account, or a path with a user name? Fixtures must be fictional or structurally redacted.
7. **People data.** Does anything rank or compare people, or put a guessed identity on a page the team reads?

Do not flag concerns no rule above covers. Zero findings is a valid result.

## Findings shape

Use the canonical fields, and add `area`, which is one of `scope`, `tier`, `ledger`, `model-boundary`, `secret`, `public-repo` or `people`:

- `location` is file:line, or the spec section.
- `trigger_condition` is the leak or weakening, and how it happens, in one line.
- `guard_snippet` is the concrete guard, type restriction or redaction.
- `potential_consequence` is what is exposed, and to whom.

No severity, priority or ranking.
