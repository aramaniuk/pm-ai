"""Re-export of `pm_ai.domain.sanitize` (AD-12, AD-29).

The type, the pattern and `sanitize()` moved into `pm_ai.domain` in story 8e:
`ModelPort` must name `Sanitized` for an unsanitized prompt to be a construction
error, and `pm_ai.ports` may import nothing but `pm_ai.domain`
(`.importlinter`'s `ports-depend-only-on-domain`). `Sanitized.__post_init__`
then needed the pattern, and `domain-imports-nothing` forbids reaching back into
`core` for it — so both travelled together.

This module stays so the path callers already import from keeps resolving. It
adds nothing: there is one definition of `sanitize`, in `pm_ai.domain.sanitize`,
and this re-binds the same objects.
"""

from __future__ import annotations

from pm_ai.domain.sanitize import (
    REDACTION,
    RULE_CHANGELOG,
    RULE_VERSION,
    FoldDisagreement,
    ForgedSanitization,
    RuleChange,
    Sanitized,
    sanitize,
)

__all__ = [
    "REDACTION",
    "RULE_CHANGELOG",
    "RULE_VERSION",
    "FoldDisagreement",
    "ForgedSanitization",
    "RuleChange",
    "Sanitized",
    "sanitize",
]
