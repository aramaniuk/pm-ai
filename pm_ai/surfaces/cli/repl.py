"""`pm-ai` with no arguments at a terminal: a prompt (story 4f, CAP-18).

The loop decides one thing about each line it reads: whether it is a command,
an ending, or an open line.

- **A command** — a first word that names one in the command table, a help
  word, or anything starting with `-` — is handed to `runner` with its words
  split as a shell splits them. `runner` is `pm_ai.app.entry.main`, injected by
  the composition root because `surfaces` may not import `app`, so the command
  runs exactly as `pm-ai <words>` does: same output, same exit code, shown
  afterwards as one status line, `exit <n>`.
- **An ending** — `exit`, `quit`, or Ctrl-D — returns 0.
- **An open line** — anything else — is answered with one sentence and
  recorded nowhere: no history file, no event log, no diagnostic log, which is
  what keeps the sentence true. It gets no `exit` status line, because the
  spec's table gives none for it: nothing was run.

Ctrl-C clears the line and shows a fresh prompt wherever it lands outside a
running command; inside one, the command's own interruption is its exit code.

Nothing is composed to open the prompt. Until `4q` sends lines through the
daemon, every command runs in this process, in the foreground, composing afresh
as the command line does (AD-21's request path does not apply yet).
"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Callable, Sequence

from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_USAGE,
    names_a_command,
)

__all__ = ["ENDINGS", "PROMPT", "run"]

PROMPT = "pm-ai> "

ENDINGS = frozenset({"exit", "quit"})
"""The words that end the session. No command in the table may be one."""


def _line_editing() -> bool:
    """Load `readline` for line editing and in-session recall, if it imports.

    Importing the module is the whole of it: `input()` uses it once loaded.
    No history file is read or written. Returns whether it loaded; without it,
    lines are still read and nothing else changes.
    """
    try:
        import readline  # noqa: F401
    except ImportError:
        return False
    return True


def _status(code: int) -> None:
    sys.stderr.flush()
    print(f"exit {code}", flush=True)


def _usage_error(sentence: str) -> None:
    print(f"pm-ai: {sentence}", file=sys.stderr, flush=True)
    _status(EXIT_USAGE)


def _open_line(named: str) -> None:
    print(
        f"`{named}` names no command (`help` lists them), and no model is in "
        f"this build to answer an open question, so this line was not sent "
        f"anywhere.",
        flush=True,
    )


def _handle(line: str, runner: Callable[[Sequence[str]], int]) -> bool:
    """Act on one line; `True` when it ends the session."""
    if not line.strip():
        return False
    try:
        words = shlex.split(line)
    except ValueError:
        _usage_error("the quote is unclosed, so nothing was run.")
        return False
    if not words:
        return False
    first = words[0]
    if first in ENDINGS:
        if len(words) == 1:
            return True
        _usage_error(f"`{first}` takes no arguments.")
        return False
    if not first:
        # `'' doctor`: an empty first word names nothing, and quoting it as
        # `` would tell the operator nothing about what they typed.
        _open_line(line.strip())
        return False
    if not (first.startswith("-") or names_a_command(first)):
        _open_line(first)
        return False
    try:
        code = runner(words)
    except KeyboardInterrupt:
        # The runner guards its own Ctrl-C; this is one that arrived outside
        # that guard. Interrupting is a deliberate stop.
        print("\npm-ai: interrupted", file=sys.stderr)
        code = EXIT_REFUSAL
    sys.stdout.flush()
    _status(code)
    return False


def run(
    runner: Callable[[Sequence[str]], int],
    *,
    read: Callable[[str], str] = input,
    line_editing: bool = False,
) -> int:
    """Read lines until `exit`, `quit` or Ctrl-D, and return 0.

    `runner` takes the words of a command and returns its exit code. `read` is
    `input`, injectable so the loop is a unit test. `line_editing` loads
    `readline` first; the composition root asks for it.
    """
    if line_editing:
        _line_editing()
    while True:
        try:
            line = read(PROMPT)
        except EOFError:
            # Ctrl-D: the session ends, on a fresh line.
            print(flush=True)
            return EXIT_OK
        except KeyboardInterrupt:
            # Ctrl-C while typing clears the line; it never ends the session.
            print(flush=True)
            continue
        except (UnicodeDecodeError, OSError) as unreadable:
            _usage_error(f"that line could not be read, so nothing was run: {unreadable}")
            continue
        try:
            if _handle(line, runner):
                return EXIT_OK
        except KeyboardInterrupt:
            # Anywhere outside `read` and the runner — the split, a sentence,
            # the status line: a fresh prompt, never the end of the session.
            print(flush=True)
