"""Spoken-command authorization (AD-32).

Auto-execution requires three independent conditions. Any one failing stages the
command as a Proposal instead — which is what stops a transcript, an untrusted
input wearing the costume of an authenticated one, conferring write access.
"""

from __future__ import annotations

from pm_ai.domain.lifecycle import UnknownVerb, lookup_verb

EXECUTE = "execute"
STAGE = "stage"


def classify(*, source_authenticated: bool, speaker_is_pm: bool, provider: str, verb: str) -> str:
    """Return EXECUTE only when source, speaker, and verb all qualify.

    The verb test is `auto_executable`, not `reversible`: a change can be
    reversible and still send an unrecallable notification, and one-tap undo
    cannot unsend an email.
    """
    if not source_authenticated:
        return STAGE  # AD-23's manual adapter is never an execution source
    if not speaker_is_pm:
        return STAGE
    try:
        if not lookup_verb(provider, verb).auto_executable:
            return STAGE
    except UnknownVerb:
        return STAGE  # fail closed — reversibility is asserted, never inferred
    return EXECUTE
