"""Which artifacts are encrypted at rest, and which are deliberately not (AD-6).

Policy only. No cipher, no key, no file I/O — a caller that has a secret decides
what to do with it. Keeping this pure is what lets it be asked about a path that
does not exist yet, which is every first write.

## The answer is declared, not inferred

Encryption is a required field on every `File` and `Collection` in the four
scope trees, alongside the tier, and `ENCRYPTED` is derived from those
declarations. So an artifact cannot be added without an encryption answer, the
way one cannot be added without a tier. The alternative — a table of rules kept
beside the trees — is the shape the tier model was moved *out* of: two edits to
add one artifact, and nothing but an import-time check catching the two drifting
apart.

## Why not a path prefix

`~/.pm-ai/private/vector_index/index.bin` is plaintext and
`~/.pm-ai/private/config.json` is encrypted. They share a parent, so any rule
reading the `private/` prefix gets one of them wrong. Classification is by what
the artifact *is*.

## Why the answer is per scope

`meetings/` under `people/` holds a direct report's 1:1 records, which the
storage contract requires encrypted. `meetings/` in a project holds summaries
committed to the repository, which the same contract requires plaintext. One
basename, two correct answers — so `ENCRYPTED` is keyed on `(scope, key)`, and
this module has to work out which scope a path is in. It does that from the
scope root names in `pm_ai.domain.scope_model` rather than from the resolver,
because `pm_ai.storage` and `pm_ai.platform` are independent siblings.

## Undeclared paths fail closed

A path no tree names is either a historical name — `event_telemetry.db` and
`chat_history/` are both former spellings still asserted by an older test — or an
artifact someone forgot to declare. Guessing plaintext on either is the guess
that leaks, so the answer is `True`. Answering with an exception instead would
turn every unrecognised path into an outage; answering *encrypted* turns it into
a file the PM cannot grep until someone declares it. The second failure is
visible and recoverable.
"""

from __future__ import annotations

from pathlib import PurePath

from pm_ai.domain.identity import ScopeKind
from pm_ai.domain.scope_model import (
    APPLICATION_DIRNAME,
    ENCLAVE_DIRNAME,
    ENCRYPTION,
    PEOPLE_DIRNAME,
    PERSONAL_DIRNAME,
    PROJECT_DIRNAME,
)

__all__ = ["is_encrypted", "scope_of"]

_PEOPLE_MARKER = (APPLICATION_DIRNAME, ENCLAVE_DIRNAME, PEOPLE_DIRNAME)


def scope_of(path: str) -> ScopeKind | None:
    """Which scope `path` belongs to, or `None` if it belongs to none.

    Ordered: the team-member scope is nested inside the application scope, so a
    path under `~/.pm-ai/private/people/` matches both markers and must be
    reported as the inner one. Checking application first would file every
    report's record under the scope documented as holding no personal records.
    """
    parts = PurePath(path).parts
    for index in range(len(parts) - len(_PEOPLE_MARKER) + 1):
        if tuple(parts[index : index + len(_PEOPLE_MARKER)]) == _PEOPLE_MARKER:
            return ScopeKind.PEOPLE
    if PERSONAL_DIRNAME in parts:
        return ScopeKind.PERSONAL
    if PROJECT_DIRNAME in parts:
        return ScopeKind.PROJECT
    if APPLICATION_DIRNAME in parts:
        return ScopeKind.APPLICATION
    return None


def is_encrypted(path: str) -> bool:
    """Whether the artifact at `path` is encrypted at rest.

    Walks the path from its last segment upward and takes the answer of the
    deepest artifact that declares one. A file inside a `Collection` — a dated
    event-log segment, a person's dossier, a capture — is not itself declared,
    so the directory that *is* declared answers for it. Structure that declares
    nothing is skipped rather than treated as a verdict, which is what stops
    `private/` answering for a child whose answer differs from its siblings'.
    """
    scope = scope_of(path)
    if scope is None:
        return True
    answers = ENCRYPTION[scope]
    for part in reversed(PurePath(path).parts):
        # A directory key carries its slash; a file key does not. Trying both is
        # cheaper than deciding from the string whether this segment is one.
        for candidate in (f"{part}/", part):
            if candidate in answers:
                return answers[candidate]
    return True
