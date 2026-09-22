"""Pre-parsing sanitization (AD-12, AD-29) — the type, the pattern, and the guard.

Non-destructive by construction: `Sanitized` holds both the untouched raw and
the derived copy, so a caller cannot accidentally overwrite the evidence a
citation resolves against.

**Here rather than in `pm_ai.core` (story 8e).** `ModelPort` has to name this
type — that is what makes an unsanitized prompt a construction error rather than
a review catch — and `.importlinter`'s `ports-depend-only-on-domain` lets
`pm_ai.ports` import nothing but `pm_ai.domain`. The pattern had to travel with
the type rather than stay behind, because `__post_init__` reads it and
`domain-imports-nothing` forbids the reverse edge. `pm_ai.core.sanitize`
re-exports both, so the module path every existing caller uses still resolves.
"""

from __future__ import annotations

import re
import typing
from dataclasses import dataclass

__all__ = ["REDACTION", "ForgedSanitization", "Sanitized", "sanitize"]

_INJECTION = re.compile(
    r"(ignore\s+(all\s+)?previous\s+instructions?"
    r"|disregard\s+(the\s+)?above"
    r"|system\s*:\s*you\s+are"
    r"|<\s*/?\s*(system|instructions?)\s*>)",
    re.IGNORECASE,
)

REDACTION = "[redacted-injection]"
"""What a matched span becomes.

Two properties make `sanitize()`'s output a fixed point, and the guard needs
both:

1. **It matches no pattern itself**, so the text substituted in cannot be
   matched and substituted again.
2. **It is non-empty**, so a substitution cannot join what stood on either side
   of the removed span into a new match. An empty replacement would delete text
   and close the gap — `ignore <system> previous instructions` would become
   `ignore  previous instructions`, a match the first pass never saw — and one
   more pass would then change the result, which is exactly what
   `__post_init__` refuses.

Story `8g` replaces the matcher with a normalised fold. Whatever it substitutes
has to keep both properties or `sanitize()` stops being able to construct its
own return type.
"""


def _redact(text: str) -> str:
    """The sanitizing transform, written once.

    **Both `sanitize()` and `__post_init__` go through here, and that is the
    point.** The guard's rule is "one more pass changes nothing", so if the
    producer and the checker each spelled the substitution themselves, a story
    that rewrote one would leave the other validating against the old rule — the
    guard would still pass, over text the current sanitizer would have altered.
    Story `8g` replaces this matcher with a normalised fold, which is exactly
    that story, so the single definition is the seam it needs.
    """
    return _INJECTION.sub(REDACTION, text)


def _is_sanitized(text: str) -> bool:
    """Whether `text` is a fixed point of `_redact` — nothing left to remove.

    The one predicate `__post_init__` asks. Derived from `_redact` rather than
    written beside it, so it cannot disagree with what the sanitizer does.
    """
    return _redact(text) == text


class ForgedSanitization(ValueError):
    """`for_model` still matches the injection pattern, so it was never sanitized.

    A `ValueError` because it is a bad argument to a constructor, and the
    constructor is the only place it can be raised.
    """


@typing.final
@dataclass(frozen=True, slots=True)
class Sanitized:
    """AD-29 — the raw is retained; only `for_model` is ever put in a prompt.

    **`__post_init__` is what makes the type worth having.** Until story 8e this
    was a public frozen dataclass with two `str` fields and no validation, so
    `Sanitized(raw=t, for_model=t)` type-checked while bypassing `sanitize()`
    entirely: a `ModelPort` typed against it would have refused a bare `str` and
    accepted the same text behind one keyword argument.

    The check is that `for_model` is a **fixed point of the sanitizer** — one
    more pass of `_INJECTION.sub` leaves it unchanged — and deliberately not
    that it equals `sanitize(raw).for_model`. Equality would refuse
    `pipelines.py`'s legitimate `ex.for_model[:80]` truncation, and every other
    honest derivation a caller makes from an already-clean string. Truncation
    cannot smuggle an injection back in: the pattern is unanchored, so any match
    in a prefix is a match in the whole.

    Forging a *clean* `for_model` — `Sanitized(raw=<anything>, for_model="hi")`
    — stays possible, and is harmless. Clean text is all a model was ever going
    to receive; what the guard exists to stop is untouched provider prose
    wearing the type.

    **`@final` closes the other way round it.** A `__post_init__` on a subclass
    overrides this one, so `class Forged(Sanitized): def __post_init__(self):
    pass` constructed over an injection string and still satisfied
    `Sequence[Sanitized]` at the port — the guard bypassed by inheritance rather
    than by a keyword argument. Static rather than runtime, because that is what
    `ModelPort`'s protection is: a subclass is refused where the mistyped call
    is, by the same checker, and there is no honest reason to specialise a pair
    of strings.
    """

    raw: str
    for_model: str

    def __post_init__(self) -> None:
        if not _is_sanitized(self.for_model):
            # **The value is not quoted.** It is provider prose by definition —
            # that is what makes it untrusted — and an exception message travels
            # into tracebacks, logs and error reporters that AD-31's disclosure
            # rules never covered. Reporting where the match is keeps the
            # refusal debuggable without copying the text somewhere new.
            match = _INJECTION.search(self.for_model)
            where = f"offset {match.start()}" if match else "an unlocated match"
            raise ForgedSanitization(
                f"for_model still matches the injection pattern at {where} of "
                f"{len(self.for_model)} characters, so it did not come through "
                f"sanitize(). Its contents are deliberately not quoted here. "
                f"Build it with `sanitize(raw)` and derive from `.for_model` — "
                f"truncating or slicing that value is fine, re-using the raw is "
                f"not."
            )

    @property
    def was_modified(self) -> bool:
        """Whether the two halves differ — **meaningful only for a pair `sanitize()` built.**

        It answers "do these two strings differ", not "did the sanitizer change
        something", and on a hand-built pair those come apart:
        `Sanitized(raw="anything", for_model="harmless")` reports True while no
        sanitizer touched either string. That construction is permitted on
        purpose — clean text is all a model was ever going to receive — so the
        caveat is the honest way to keep both. Read it only off `sanitize(t)`,
        where the two halves have the derivation that makes the answer mean what
        it says.
        """
        return self.raw != self.for_model


def sanitize(raw: str) -> Sanitized:
    return Sanitized(raw=raw, for_model=_redact(raw))
