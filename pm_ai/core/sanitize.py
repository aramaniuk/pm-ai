"""Pre-parsing sanitization (AD-12, AD-29).

Non-destructive by construction: `Sanitized` holds both the untouched raw and
the derived copy, so a caller cannot accidentally overwrite the evidence a
citation resolves against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_INJECTION = re.compile(
    r"(ignore\s+(all\s+)?previous\s+instructions?"
    r"|disregard\s+(the\s+)?above"
    r"|system\s*:\s*you\s+are"
    r"|<\s*/?\s*(system|instructions?)\s*>)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Sanitized:
    """AD-29 — the raw is retained; only `for_model` is ever put in a prompt."""

    raw: str
    for_model: str

    @property
    def was_modified(self) -> bool:
        return self.raw != self.for_model


def sanitize(raw: str) -> Sanitized:
    return Sanitized(raw=raw, for_model=_INJECTION.sub("[redacted-injection]", raw))
