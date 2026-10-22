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

**Jobs at the prompt** (story 4v). `Live` is the session a board of running
jobs (`repl._Jobs`) is shown on: the prompt's message and the job lines below
the hint are read from the board each time the library draws, a waiting thread
prints above the prompt through `above` (the library's `run_in_terminal`,
scheduled on the session's own event loop) and asks for a redraw through
`redraw` (the library's thread-safe `invalidate`). While a job runs, a task on
the session's loop redraws about four times a second, started when a read
opens and ending when none runs; with none running nothing redraws. A redraw
is drawing, not work: nothing here schedules anything (AD-7).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence
from typing import Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import set_app
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
from prompt_toolkit.utils import (
    get_bell_environment_variable,
    get_cwidth,
    get_term_environment_variable,
)

from pm_ai.surfaces.cli.completion import Completion

__all__ = ["Board", "Live", "build", "session", "terminal_output"]

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
                display_meta=candidate.meta or None,
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
    toolbar: Callable[[], str] | None = None,
) -> PromptSession[str]:
    """The session over this terminal; `input` and `output` are a test's when given.

    `toolbar` draws the bottom of the terminal (story 4v); without it, the hint.
    """
    prompt_session: PromptSession[str] = PromptSession(
        completer=_Completer(completion),
        complete_while_typing=False,
        auto_suggest=AutoSuggestFromHistory(),
        history=_Commands(keep),
        enable_history_search=False,
        bottom_toolbar=toolbar or (lambda: _hint(completion, prompt_session)),
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


class Board(Protocol):
    """What `Live` shows of the jobs running (story 4v); `repl._Jobs` is one."""

    def prompt(self, text: str) -> str:
        """The prompt's text, with the count of running jobs when there are any."""
        ...

    def lines(self, columns: int, cells: Callable[[str], int]) -> Sequence[str]:
        """One line per running job, under `columns` cells measured by `cells`."""
        ...

    def running(self) -> int:
        """How many acknowledged jobs have no result yet."""
        ...

    def opened(self) -> None:
        """A read of a line has started: arrivals may be printed above it now."""
        ...

    def closed(self) -> None:
        """The read has ended: arrivals are held until the next one opens."""
        ...


class Live:
    """A prompt over this terminal: call it with the prompt text, get one line.

    Raises `KeyboardInterrupt` for Ctrl-C and `EOFError` for Ctrl-D at an
    empty line, as `input` does. `show` puts a board of running jobs on it
    (story 4v); with none shown, or none running, it draws what `4u` drew.
    """

    def __init__(
        self,
        completion: Completion,
        *,
        keep: Callable[[str], bool],
        input: Input | None = None,
        output: Output | None = None,
    ) -> None:
        self._completion = completion
        self._board: Board | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.refreshing = False
        """Whether the redraw task is running (story 4v)."""
        self.session = build(
            completion, keep=keep, input=input, output=output, toolbar=self.shown_toolbar
        )

    def show(self, board: Board | None) -> None:
        """Show `board` from the next read on; `None` stops showing one."""
        self._board = board

    def __call__(self, text: str) -> str:
        try:
            # Styled with the terminal's own foreground, which the renderer
            # counts as a style, so it writes the trailing space rather than
            # skipping it as blank: `pm-ai> ` reaches the terminal as those
            # bytes. A callable, so the count is read each time it is drawn.
            return self.session.prompt(
                lambda: [(_PROMPT_CLASS, self.shown_prompt(text))], pre_run=self._opened
            )
        finally:
            board = self._board
            if board is not None:
                board.closed()
            self._loop = None

    # ── what is drawn ────────────────────────────────────────────────────────

    def shown_prompt(self, text: str) -> str:
        """The prompt as it is drawn now."""
        board = self._board
        if board is None:
            return text
        try:
            return board.prompt(text)
        except Exception:  # noqa: BLE001 — drawing never prints an error
            return text

    def shown_toolbar(self) -> str:
        """The bottom of the terminal as it is drawn now: the hint, then the jobs."""
        hint = _hint(self._completion, self.session)
        board = self._board
        if board is None:
            return hint
        try:
            jobs = list(board.lines(self.session.output.get_size().columns, get_cwidth))
        except Exception:  # noqa: BLE001 — drawing never prints an error
            jobs = []
        return "\n".join([hint, *jobs] if hint else jobs)

    # ── called from the threads that wait on jobs ────────────────────────────

    def above(self, write: Callable[[], None]) -> None:
        """Run `write` above the prompt being read, on the session's own loop.

        Thread-safe. Raises `RuntimeError` when no read is running, or its loop
        has closed; then the caller holds what it would have printed.
        """
        loop = self._loop
        if loop is None:
            raise RuntimeError("no line is being read")
        loop.call_soon_threadsafe(self._above, write)

    def redraw(self) -> None:
        """Ask the session to draw again; thread-safe, nothing when no read runs."""
        self.session.app.invalidate()

    # ── on the session's loop ────────────────────────────────────────────────

    def _opened(self) -> None:
        self._loop = asyncio.get_running_loop()
        board = self._board
        if board is None:
            return
        board.opened()
        if board.running() > 0:
            self.session.app.create_background_task(self._refresh(board))

    def _above(self, write: Callable[[], None]) -> None:
        app = self.session.app
        if not app.is_running or app.is_done:
            # The line was accepted meanwhile: what `write` would print is
            # still held, and printed before the next prompt.
            return
        with set_app(app):
            run_in_terminal(write)

    async def _refresh(self, board: Board) -> None:
        """Redraw while a job runs; return once none does (story 4v)."""
        # Read when it runs, so the interval is the one `repl` names.
        from pm_ai.surfaces.cli import repl

        self.refreshing = True
        try:
            while board.running() > 0:
                await asyncio.sleep(repl.REDRAW_SECONDS)
                self.redraw()
        finally:
            self.refreshing = False


def session(
    completion: Completion,
    *,
    keep: Callable[[str], bool],
    input: Input | None = None,
    output: Output | None = None,
) -> Live:
    """A prompt over this terminal: call it with the prompt text, get one line."""
    return Live(completion, keep=keep, input=input, output=output)


def _hint(completion: Completion, prompt_session: PromptSession[str]) -> str:
    try:
        return completion.hint(prompt_session.default_buffer.text) or ""
    except Exception:  # noqa: BLE001 — a hint never prints an error
        return ""
