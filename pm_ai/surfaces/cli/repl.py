"""`pm-ai` with no arguments at a terminal: a prompt (stories 4f and 4q, CAP-18).

The loop decides two things about each line it reads: what it is — a command,
an ending, or an open line — and, for a command, where it runs.

- **A command** — a first word that names one in the command table, a help
  word, or anything starting with `-` — runs exactly as `pm-ai <words>` does:
  same output, same exit code, shown afterwards as one status line, `exit <n>`.
  It runs in one of two places (story 4q, AD-7, AD-8):

  - **in this process**, through `runner`, when it needs this terminal — it
    asks questions or reads a secret (`TERMINAL_BOUND`) — or when the command
    table marks it as needing no daemon (`Command.target` of `None`: `doctor`,
    `setup`, `project add`). Help words and words starting with `-` are
    answered here too: the command line answers them without composing
    anything, and they name no command for the daemon to run. `runner` is
    `pm_ai.app.entry.main`, injected because `surfaces` may not import `app`;
  - **in the daemon**, through `client`, for every other command: the line goes
    to `4r`'s prompt endpoint and its output and exit code come back. A command
    whose declared bound is five seconds or more is acknowledged with a job id
    instead (AD-21); the prompt returns at once, and the result is printed,
    prefixed by the id, at the next prompt — never into a line half typed.

  A command added to the table later goes through the daemon unless it is
  named as needing this terminal.
- **An ending** — `exit`, `quit`, or Ctrl-D — returns 0, naming any job still
  running, which continues in the daemon.
- **An open line** — anything else — is answered with one sentence and
  recorded nowhere: no history file, no event log, no diagnostic log, and
  never sent to the daemon.

Ctrl-C clears the line and shows a fresh prompt wherever it lands outside a
running command; inside a local one, the command's own interruption is its exit
code; while waiting on the daemon, it abandons the wait and the daemon finishes
the command on its own.

Nothing is composed and nothing is sent to open the prompt. The one thread this
module starts waits on a job the daemon already owns and decides nothing about
when work runs (AD-7): the prompt schedules nothing.
"""

from __future__ import annotations

import queue
import shlex
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_UNEXPECTED,
    EXIT_USAGE,
    TABLE,
    TAKES_A_PATH,
    TERMINAL_BOUND,
    Answered,
    names_a_command,
    parse,
)
from pm_ai.surfaces.cli.loopback import (
    Acknowledged,
    Answer,
    Followed,
    Outcome,
    StreamLost,
)

__all__ = ["DaemonClient", "ENDINGS", "PROMPT", "run", "runs_here"]

PROMPT = "pm-ai> "

ENDINGS = frozenset({"exit", "quit"})
"""The words that end the session. No command in the table may be one."""


class DaemonClient(Protocol):
    """What the prompt needs of `pm_ai.surfaces.cli.loopback.LoopbackClient`."""

    def send(self, line: str) -> Outcome: ...

    def follow(self, job_id: str) -> Followed: ...


def runs_here(words: Sequence[str]) -> bool:
    """Whether a command's words run in this process rather than in the daemon.

    One rule, read off the command table rather than a second list: a command
    that needs this terminal (`TERMINAL_BOUND`), or one the table marks as
    needing no daemon (`Command.target` of `None`). Words the command line
    answers without running anything — help, a usage error such as a group
    typed alone, an unknown leaf or option — are answered here too, by the
    command line, which composes nothing for them: they need no daemon, and
    the answer is the same with or without one. Everything else goes through
    the daemon.
    """
    if not words:
        return True
    command = TABLE.get(words[0])
    if command is None or isinstance(parse(words), Answered):
        return True
    if any(tuple(words[: len(spelled)]) == spelled for spelled in TERMINAL_BOUND):
        return True
    leaf = command if command.run is not None else (
        command.leaves.get(words[1]) if len(words) > 1 else None
    )
    return leaf is not None and leaf.run is not None and leaf.target is None


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


class _Jobs:
    """The jobs this session was acknowledged, and the results that have arrived.

    One thread per job waits on the daemon's stream for it and hands the result
    to `_arrived`; only the loop prints, and only between lines.
    """

    def __init__(self, client: DaemonClient) -> None:
        self._client = client
        self._waiting: dict[str, threading.Thread] = {}
        self._arrived: queue.SimpleQueue[tuple[str, Followed]] = queue.SimpleQueue()

    def follow(self, job_id: str) -> None:
        if job_id in self._waiting:
            # The daemon answered with a job already in flight (one job per
            # kind and scope): its result is already on its way here.
            return
        waiter = threading.Thread(
            target=self._wait, args=(job_id,), name=f"pm-ai-follow-{job_id}", daemon=True
        )
        self._waiting[job_id] = waiter
        waiter.start()

    def waiting(self, job_id: str) -> bool:
        """Whether the job's result has not been printed yet."""
        return job_id in self._waiting

    def in_flight(self) -> list[str]:
        return [job for job, waiter in self._waiting.items() if waiter.is_alive()]

    def deliver(self) -> None:
        """Print every result that has arrived, each line prefixed by its job id."""
        while True:
            try:
                job_id, followed = self._arrived.get_nowait()
            except queue.Empty:
                return
            self._waiting.pop(job_id, None)
            if isinstance(followed, Answer):
                for line in followed.output.splitlines():
                    print(f"{job_id}: {line}")
                print(f"{job_id}: exit {followed.code}", flush=True)
            else:
                print(f"pm-ai: {followed.sentence}", file=sys.stderr, flush=True)

    def _wait(self, job_id: str) -> None:
        try:
            followed = self._client.follow(job_id)
        except Exception as error:  # noqa: BLE001 — a lost stream, said by name
            followed = StreamLost(job_id, type(error).__name__)
        self._arrived.put((job_id, followed))


def _absolute_paths(words: Sequence[str]) -> list[str]:
    """`words`, with a command's path argument made absolute against this directory.

    The daemon does not run in the directory the line was typed in, and refuses
    a relative path (`TAKES_A_PATH`, story 4r); the prompt does, so it resolves
    the path as `pm-ai <words>` here would before sending. The words are rebuilt
    from the one parse of them, so an option or a flag before the path changes
    nothing.
    """
    if tuple(words[:2]) not in TAKES_A_PATH:
        return list(words)
    parsed = parse(words)
    if isinstance(parsed, Answered) or not parsed.arguments:
        return list(words)
    given, *rest = parsed.arguments
    path = Path(given).expanduser()
    if path.is_absolute():
        return list(words)
    options = [part for name, value in parsed.options.items() for part in (f"--{name}", value)]
    # Story 4t: value-less flags are rebuilt too, or one would be lost on the
    # way to the daemon.
    flags = [f"--{flag}" for flag in sorted(parsed.flags)]
    return [*words[:2], str(Path.cwd() / path), *rest, *options, *flags]


def _through_daemon(words: Sequence[str], client: DaemonClient, jobs: _Jobs) -> None:
    """Send a command's words to the daemon and show what comes back."""
    try:
        line = shlex.join(_absolute_paths(words))
    except OSError as unreadable:
        print(
            f"pm-ai: this prompt's working directory could not be read "
            f"({unreadable.strerror or unreadable}), so the relative path could "
            f"not be made absolute and this line was not sent.",
            file=sys.stderr,
            flush=True,
        )
        _status(EXIT_REFUSAL)
        return
    try:
        outcome = client.send(line)
    except KeyboardInterrupt as interrupted:
        if getattr(interrupted, "sent", True):
            said = (
                "stopped waiting for the daemon; it finishes the command on its "
                "own, and its output is not shown here."
            )
        else:
            said = "interrupted before the line reached the daemon, so it was not sent."
        print(f"\npm-ai: {said}", file=sys.stderr, flush=True)
        _status(EXIT_REFUSAL)
        return
    except Exception as error:  # noqa: BLE001 — said by name; the session continues
        print(
            f"pm-ai: sending this line to the daemon stopped on an unexpected "
            f"{type(error).__name__}, so whether it ran is not known.",
            file=sys.stderr,
            flush=True,
        )
        _status(EXIT_UNEXPECTED)
        return
    if isinstance(outcome, Answer):
        sys.stdout.write(outcome.output)
        sys.stdout.flush()
        _status(outcome.code)
        return
    if isinstance(outcome, Acknowledged):
        jobs.follow(outcome.job_id)
        print(
            f"{outcome.job_id}: acknowledged; the daemon runs it, and its result "
            f"is shown here, prefixed by this id, at a prompt once it is ready.",
            flush=True,
        )
        running = jobs.in_flight()
        if len(running) > 1:
            print(f"jobs in flight: {', '.join(running)}", flush=True)
        return
    print(f"pm-ai: {outcome.sentence}", file=sys.stderr, flush=True)
    _status(EXIT_UNEXPECTED)


def _handle(
    line: str,
    runner: Callable[[Sequence[str]], int],
    client: DaemonClient | None,
    jobs: _Jobs | None,
) -> bool:
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
    if client is not None and jobs is not None and not runs_here(words):
        _through_daemon(words, client, jobs)
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


def _ending(jobs: _Jobs | None) -> int:
    """Show what has arrived, name each job still running, and end with 0.

    The jobs in flight are read first and the results delivered after, then
    once more: a job finishing in between is printed, never lost between the
    two reads.
    """
    if jobs is not None:
        running = jobs.in_flight()
        jobs.deliver()
        jobs.deliver()
        for job_id in [job for job in running if jobs.waiting(job)]:
            print(
                f"{job_id} is still running; it continues in the daemon, and its "
                f"result is not shown here.",
                flush=True,
            )
    return EXIT_OK


def run(
    runner: Callable[[Sequence[str]], int],
    *,
    read: Callable[[str], str] = input,
    line_editing: bool = False,
    client: DaemonClient | None = None,
) -> int:
    """Read lines until `exit`, `quit` or Ctrl-D, and return 0.

    `runner` takes the words of a command and returns its exit code. `client`
    is the daemon's (story 4q); without one every command runs through
    `runner`, as before `4q`. `read` is `input`, injectable so the loop is a
    unit test. `line_editing` loads `readline` first; the composition root
    asks for it.
    """
    if line_editing:
        _line_editing()
    jobs = None if client is None else _Jobs(client)
    while True:
        try:
            if jobs is not None:
                # Between lines only: a result never lands in a line half typed.
                jobs.deliver()
            line = read(PROMPT)
        except EOFError:
            # Ctrl-D: the session ends, on a fresh line.
            print(flush=True)
            return _ending(jobs)
        except KeyboardInterrupt:
            # Ctrl-C while typing clears the line; it never ends the session.
            print(flush=True)
            continue
        except (UnicodeDecodeError, OSError) as unreadable:
            _usage_error(f"that line could not be read, so nothing was run: {unreadable}")
            continue
        try:
            if _handle(line, runner, client, jobs):
                return _ending(jobs)
        except KeyboardInterrupt:
            # Anywhere outside `read` and the runner — the split, a sentence,
            # the status line: a fresh prompt, never the end of the session.
            print(flush=True)
