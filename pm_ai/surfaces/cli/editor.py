"""The `pm-ai>` prompt's line editor over `prompt_toolkit` (story 4u).

Imported only by `repl`, when the prompt opens — never at module import — so
`pm-ai <command>` never loads the library, and a missing runtime extra is an
`ImportError` the prompt answers with `4q`'s loop.

What the session is, and what it is not:

- **Tab** completes from `completion.Completion`, and only on Tab: nothing is
  completed while typing, so the readers of local files run at the first Tab
  that needs them.
- **The bottom line** shows `Completion.hint` for the line being typed.
- **A dim suggestion** offers the rest of a matching earlier command from this
  session; the right arrow accepts it (the library's own binding).
- **History** is in memory and holds only command lines (`keep`). No history
  file is read or written. Up arrow recalls the previous command.
- **The cursor-position request is off** (`Vt100_Output.enable_cpr`, checked
  against 3.0.53): a terminal that never answers it neither delays the prompt
  nor gets the library's warning. Without an answer the library does not know
  how many rows lie below the cursor and would never draw the bottom line, so
  the output says one: the cursor's own row, the least that is always true.
- **What starts other processes or stops this one is off**, explicitly:
  opening the line in an editor, the system-command prompt, and suspending
  with Ctrl-Z, which is bound to do nothing rather than insert itself as text.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import CompleteEvent, Completer
from prompt_toolkit.completion import Completion as Offered
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import Input, create_input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.output import Output
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_bell_environment_variable, get_term_environment_variable

from pm_ai.surfaces.cli.completion import Completion

__all__ = ["build", "session", "terminal_output"]

_PROMPT_CLASS = "class:prompt"

_STYLE = Style.from_dict(
    {"prompt": "fg:ansidefault", "bottom-toolbar": "noreverse", "auto-suggestion": "#888888"}
)
"""The bottom line in the terminal's own colours, so an empty hint draws nothing."""


def _bindings() -> KeyBindings:
    """Ctrl-Z does nothing: with suspend off the library would insert it as text."""
    bindings = KeyBindings()

    @bindings.add("c-z")
    def _ignored(event: KeyPressEvent) -> None:
        return None

    return bindings


class _Output(Vt100_Output):
    """The terminal, with no cursor-position request and one known row below it."""

    def get_rows_below_cursor_position(self) -> int:
        # The CPR answer would say how many rows lie below the cursor; with
        # the request off, the cursor's own row is what is known.
        return 1


class _Commands(InMemoryHistory):
    """This session's history, in memory, keeping only the lines `keep` admits."""

    def __init__(self, keep: Callable[[str], bool]) -> None:
        super().__init__()
        self._keep = keep

    def append_string(self, string: str) -> None:
        if self._keep(string):
            super().append_string(string)


class _Completer(Completer):
    """`Completion.complete`, offered to the library; a fault offers nothing."""

    def __init__(self, completion: Completion) -> None:
        self._completion = completion

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Offered]:
        before = document.text_before_cursor
        try:
            candidates = self._completion.complete(before)
        except Exception:  # noqa: BLE001 — completion never prints an error
            return
        for candidate in candidates:
            yield Offered(
                candidate.text,
                start_position=candidate.start - len(before),
                display=candidate.display,
            )


def terminal_output() -> Vt100_Output:
    """This terminal's output, built as the library's `create_output` builds it, CPR off."""
    import sys

    built = _Output.from_pty(
        sys.stdout,
        term=get_term_environment_variable(),
        default_color_depth=ColorDepth.from_env(),
        enable_bell=get_bell_environment_variable(),
    )
    built.enable_cpr = False
    return built


def build(
    completion: Completion,
    *,
    keep: Callable[[str], bool],
    input: Input | None = None,
    output: Output | None = None,
) -> PromptSession[str]:
    """The session over this terminal; `input` and `output` are a test's when given."""
    prompt_session: PromptSession[str] = PromptSession(
        completer=_Completer(completion),
        complete_while_typing=False,
        auto_suggest=AutoSuggestFromHistory(),
        history=_Commands(keep),
        enable_history_search=False,
        bottom_toolbar=lambda: _hint(completion, prompt_session),
        style=_STYLE,
        # Nothing that starts another process or stops this one (story 4u).
        enable_open_in_editor=False,
        enable_system_prompt=False,
        enable_suspend=False,
        key_bindings=_bindings(),
        mouse_support=False,
        input=input if input is not None else create_input(),
        output=output if output is not None else terminal_output(),
    )
    return prompt_session


def session(
    completion: Completion,
    *,
    keep: Callable[[str], bool],
    input: Input | None = None,
    output: Output | None = None,
) -> Callable[[str], str]:
    """A prompt over this terminal: call it with the prompt text, get one line.

    Raises `KeyboardInterrupt` for Ctrl-C and `EOFError` for Ctrl-D at an
    empty line, as `input` does.
    """
    prompt_session = build(completion, keep=keep, input=input, output=output)

    def read(text: str) -> str:
        # Styled with the terminal's own foreground, which the renderer counts
        # as a style, so it writes the trailing space rather than skipping it
        # as blank: `pm-ai> ` reaches the terminal as those bytes.
        return prompt_session.prompt([(_PROMPT_CLASS, text)])

    return read


def _hint(completion: Completion, prompt_session: PromptSession[str]) -> str:
    try:
        return completion.hint(prompt_session.default_buffer.text) or ""
    except Exception:  # noqa: BLE001 — a hint never prints an error
        return ""
