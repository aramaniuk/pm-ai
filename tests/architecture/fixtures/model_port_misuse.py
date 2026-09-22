"""Deliberately mistyped uses of the model boundary, checked by mypy as a subprocess.

Not collected by pytest and not checked by a bare `uv run mypy`: `[tool.mypy]`
declares `files = ["pm_ai"]`, so nothing under `tests/` is checked unless a path
is passed explicitly. `test_sanitize_boundary.py` passes this one, which is what
turns "omitting sanitization is a construction error" — and, since 2026-09-22,
"omitting the task class is one too" — into something asserted rather than
asserted-about.

**The markers are the contract.** A trailing `# refused: <mypy-code>` comment on
a line of **code** means that line must draw exactly that error code; every other
line must draw none. The test reads them from this file, so a new case is added
here and nowhere else, and a case that stops being refused fails rather than
quietly passing.

Code lines only, and the reader is parsing real comment tokens to enforce it. The
same words inside a docstring or a string literal — this paragraph, for instance
— are prose about the convention, not an instance of it, and mypy would never
report at that line, so a marker there could only ever be an expectation nothing
can satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass

from pm_ai.domain.sanitize import Sanitized, sanitize
from pm_ai.domain.task_classes import TaskClass
from pm_ai.ports import ModelPort

# ── The type cannot be specialised out of its guard ──────────────────────────


@dataclass(frozen=True, slots=True)
class ForgedBySubclassing(Sanitized):  # refused: misc
    """A subclass overrides `__post_init__`, and the fixed-point check with it.

    This constructed over an injection string and satisfied
    `Sequence[Sanitized]` at the port until `Sanitized` was made `@final` — the
    guard bypassed by inheritance rather than by a keyword argument. `@final` is
    a static claim, so this is the checker that enforces it.
    """

    def __post_init__(self) -> None:
        return None


# ── AD-12: externally-sourced text is `Sanitized`, never `str` ───────────────


def omits_sanitization(model: ModelPort, provider_text: str) -> str:
    """The omission AD-12's consumer clause exists to catch."""
    return model.complete(task_class=TaskClass.CLASSIFICATION, instructions="Do.", external=provider_text)  # refused: arg-type


def assembles_the_prompt_itself(model: ModelPort, clean: Sanitized) -> str:
    """Interpolating into a template flattens the type back to `str`."""
    return model.complete(task_class=TaskClass.COACHING, instructions="Do.", external=f"T: {clean.for_model}")  # refused: arg-type


def one_unsanitized_fragment(model: ModelPort, provider_text: str) -> str:
    """A fragment list is checked per element, so one raw string is enough."""
    return model.complete(task_class=TaskClass.RESEARCH, instructions="Do.", external=[provider_text])  # refused: list-item


def reads_the_raw_back_out(model: ModelPort, clean: Sanitized) -> str:
    """AD-29 keeps `raw` for citation resolution; it is not a prompt fragment."""
    return model.complete(task_class=TaskClass.RESEARCH, instructions="Do.", external=[clean.raw])  # refused: list-item


def unwraps_the_clean_half(model: ModelPort, clean: Sanitized) -> str:
    """The near-miss most likely to be written by hand, and it must still fail.

    `for_model` is the right string, so this call would work — which is why a
    reviewer waves it through. It is refused anyway: the pair is what carries
    `raw` to the citation resolver, and unwrapping it one line early is how the
    port stops being able to tell a derived string from any other.
    """
    return model.complete(task_class=TaskClass.RESEARCH, instructions="Do.", external=[clean.for_model])  # refused: list-item


# ── AD-15: the call site declares its task class ─────────────────────────────


def omits_the_task_class(model: ModelPort, clean: Sanitized) -> str:
    """"A call without a declared task class is a defect" — AD-15's invariant row."""
    return model.complete(instructions="Do.", external=[clean])  # refused: call-arg


def spells_the_task_class_as_a_string(model: ModelPort, clean: Sanitized) -> str:
    """Typed rather than `str`, so a misspelling is caught here and not by a router."""
    return model.complete(task_class="coaching", instructions="Do.", external=[clean])  # refused: arg-type


def passes_the_task_class_positionally(model: ModelPort, clean: Sanitized) -> str:
    """Keyword-only, so the declaration cannot drift into another slot."""
    return model.complete(TaskClass.COACHING, instructions="Do.", external=[clean])  # refused: call-arg


# ── The shapes that must type-check ──────────────────────────────────────────


def sanitized_fragments(model: ModelPort, provider_text: str) -> str:
    """The accepted shape — no marker, so mypy must report nothing here."""
    return model.complete(
        task_class=TaskClass.EXTRACTION,
        instructions="Do.",
        external=[sanitize(provider_text)],
    )


def internal_text_may_be_a_plain_str(model: ModelPort) -> str:
    """`instructions` is prose pm-ai wrote about itself: no outside author."""
    return model.complete(
        task_class=TaskClass.BRIEFING_SYNTHESIS, instructions="Summarise the week.", external=()
    )


def a_class_added_later_is_an_ordinary_call(model: ModelPort, clean: Sanitized) -> str:
    """The vocabulary is open, and the port makes no claim about which members exist.

    Nothing in this signature enumerates `TaskClass`, so a member a later story
    adds reaches the port the same way these do. Written against an existing
    member because a future one cannot be spelled yet; what it pins is that the
    port constrains the *type*, never the membership.
    """
    return model.complete(
        task_class=TaskClass.FUZZY_MATCH, instructions="Do.", external=[clean]
    )
