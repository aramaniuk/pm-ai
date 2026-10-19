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
    prefixed by the id, as soon as it arrives (story 4v): above the line being
    typed, which is drawn again intact. While a local command runs or the
    prompt waits on an inline answer, a result is held and printed when the
    prompt returns, before it is drawn — never into a command's own questions.
    The prompt counts the jobs running (`pm-ai [2 running]> `) and the lines
    below the hint show each, with its state and the time since it was
    acknowledged on this process's monotonic clock.

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

**Line editing** (story 4u). At a terminal the lines are read through
`prompt_toolkit`, imported when the prompt opens and never at module import:
Tab completes from the command table and a few readers of local files
(`completion`), the bottom line shows the usage of the command being typed, and
a dim suggestion offers the rest of a command typed earlier in this session,
accepted with the right arrow. History is this session's, in memory, and holds
only lines whose first word names a command. Without the library — the runtime
extra not installed, or a session that cannot start — the prompt is `4q`'s loop
over `input`, said once in one sentence, and the session continues; results
are then printed at the next prompt, as `4q` printed them.

Nothing is composed and nothing is sent to open the prompt. The one thread this
module starts waits on a job the daemon already owns and decides nothing about
when work runs (AD-7): the prompt schedules nothing. The redraw while a job runs
is drawing, not work.
"""

from __future__ import annotations

import os
import shlex
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pm_ai.surfaces.cli.completion import Completion
from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_UNEXPECTED,
    EXIT_USAGE,
    TABLE,
    TAKES_A_PATH,
    TERMINAL_BOUND,
    DROP_WORDS,
    Answered,
    Refusal,
    ask_drop_here,
    drop_would_ask,
    names_a_command,
    parse,
)
from pm_ai.surfaces.cli.loopback import (
    FINISHED,
    Acknowledged,
    Answer,
    Followed,
    Outcome,
    StreamLost,
    Unreached,
)

__all__ = [
    "DaemonClient",
    "ENDINGS",
    "NO_LINE_EDITOR",
    "NotATerminal",
    "PROMPT",
    "is_a_command_line",
    "line_editor_failed",
    "run",
    "runs_here",
]

PROMPT = "pm-ai> "

ENDINGS = frozenset({"exit", "quit"})
"""The words that end the session. No command in the table may be one."""


class DaemonClient(Protocol):
    """What the prompt needs of `pm_ai.surfaces.cli.loopback.LoopbackClient`."""

    def send(self, line: str) -> Outcome: ...

    def reachable(self) -> Unreached | None:
        """`None` when a line could be sent now; else why not (story 11d)."""
        ...

    def follow(
        self, job_id: str, on_state: Callable[[str], None] | None = None
    ) -> Followed: ...


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

    `4q`'s loop, kept for a prompt without `prompt_toolkit` (story 4u): no
    completion of its own is set, so there is one completer, not two that must
    agree. Importing the module is the whole of it: `input()` uses it once
    loaded. No history file is read or written. Returns whether it loaded;
    without it, lines are still read and nothing else changes.
    """
    try:
        import readline  # noqa: F401
    except ImportError:
        return False
    return True


NO_LINE_EDITOR = (
    "pm-ai: completion and hints need the runtime extra (`uv sync --extra "
    "runtime`); this prompt reads lines without them."
)
"""Said once, at open, when `prompt_toolkit` cannot be imported (story 4u)."""


def line_editor_failed(error: BaseException, *, started: bool = False) -> str:
    """The one sentence for a line editor that imported but could not run, by type."""
    if started:
        return (
            f"pm-ai: the line editor stopped on {type(error).__name__}, so this "
            f"prompt reads lines without completion or hints from here on."
        )
    return (
        f"pm-ai: the line editor could not start ({type(error).__name__}), so "
        f"this prompt reads lines without completion or hints."
    )


def _is_a_terminal(stream: object) -> bool:
    """Whether `stream` is backed by a terminal's file descriptor.

    Asked of the descriptor, not of `isatty()` alone: a line editor reads and
    writes the descriptor itself, so a stream without one cannot carry it.
    """
    try:
        return os.isatty(stream.fileno())  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        return False


class NotATerminal(Exception):
    """Standard input or output is not a terminal, so no line editor is started."""


def is_a_command_line(line: str) -> bool:
    """Whether `line`'s first word names a command, as `_handle` decides it.

    The in-memory history keeps only these (story 4u, `4f`'s rule that an open
    line is recorded nowhere), and the completion readers are read again after
    one runs.
    """
    try:
        words = shlex.split(line)
    except ValueError:
        return False
    if not words or not words[0]:
        return False
    first = words[0]
    return first not in ENDINGS and (first.startswith("-") or names_a_command(first))


_EDITOR_PACKAGES = frozenset({"prompt_toolkit", "wcwidth"})
"""The runtime extra's line editor and its one dependency (story 4u)."""


_input: Callable[[str], str] = input
"""`4q`'s reader, bound when this module loads: a command's own questions call
`input` by name, and a stand-in for them must not become the prompt's reader."""


@runtime_checkable
class _Screen(Protocol):
    """What the jobs need of the line editor's session (`editor.Live`, story 4v)."""

    def show(self, board: _Jobs | None) -> None: ...

    def above(self, write: Callable[[], None]) -> None: ...

    def redraw(self) -> None: ...


class _Edited:
    """`prompt_toolkit`'s `prompt`, falling back to `input` if the library fails.

    Ctrl-C and Ctrl-D reach the loop as they do from `input`. Any other
    exception from the library is said once, by type, and every later line is
    read by `input`: the session continues. From then on the jobs are `4q`'s:
    results at the next prompt (story 4v).
    """

    def __init__(self, prompt: Callable[[str], str]) -> None:
        self._prompt: Callable[[str], str] | None = prompt
        self._screen: _Screen | None = prompt if isinstance(prompt, _Screen) else None
        self._jobs: _Jobs | None = None

    def attach(self, jobs: _Jobs) -> None:
        """Show `jobs` on the session, when the session can show them."""
        if self._screen is None:
            return
        self._jobs = jobs
        jobs.attach(self._screen)
        self._screen.show(jobs)

    def __call__(self, text: str) -> str:
        if self._prompt is None:
            return _input(text)
        try:
            return self._prompt(text)
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as failed:  # noqa: BLE001 — said by type; the session continues
            self._prompt = None
            jobs = self._jobs
            self._detach()
            print(line_editor_failed(failed, started=True), file=sys.stderr, flush=True)
            _line_editing()
            if jobs is not None:
                # What arrived while the failed read was open, printed once,
                # before `4q`'s prompt.
                jobs.deliver()
            return _input(text)

    def _detach(self) -> None:
        if self._jobs is not None:
            self._jobs.attach(None)
        if self._screen is not None:
            try:
                self._screen.show(None)
            except Exception:  # noqa: BLE001, S110 — the session has already failed
                pass
        self._screen = None
        self._jobs = None


def _line_editor(completion: Completion) -> Callable[[str], str] | _Edited:
    """The reader the prompt uses: `prompt_toolkit`'s when it imports and starts.

    The import is here, when the prompt opens, so `pm-ai <command>` never
    loads the library. A failed import is `4q`'s loop and the sentence naming
    the runtime extra; a session that cannot start is `4q`'s loop and one
    sentence naming what failed by type.
    """
    try:
        from pm_ai.surfaces.cli import editor
    except Exception as failed:  # noqa: BLE001 — said; the session continues
        if (
            isinstance(failed, ModuleNotFoundError)
            and (failed.name or "").partition(".")[0] in _EDITOR_PACKAGES
        ):
            # The runtime extra is not installed.
            print(NO_LINE_EDITOR, file=sys.stderr, flush=True)
        else:
            # Installed but incompatible (`cannot import name ...`), or a fault
            # in pm-ai's own module: said by type, never a traceback.
            print(line_editor_failed(failed), file=sys.stderr, flush=True)
        _line_editing()
        return _input
    try:
        if not (_is_a_terminal(sys.stdin) and _is_a_terminal(sys.stdout)):
            raise NotATerminal("standard input or output is not a terminal")
        prompt = editor.session(completion, keep=is_a_command_line)
    except Exception as failed:  # noqa: BLE001 — said by type; the session continues
        print(line_editor_failed(failed), file=sys.stderr, flush=True)
        _line_editing()
        return _input
    return _Edited(prompt)


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


JOB_LINES = 3
"""The most job lines shown below the hint; the rest are `+N more` (story 4v)."""

REDRAW_SECONDS = 0.25
"""How often the session draws the prompt and the job lines again while a job
runs (`editor.Live`); the spinner moves one frame each time (story 4v)."""

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

STATE_CELLS = 16
"""The most cells a job's state takes on its line; a longer one is cut with `…`."""

_ACKNOWLEDGED = "acknowledged"
"""A job's state before its stream's first event: all the prompt knows of it."""


def elapsed(seconds: float) -> str:
    """`m:ss` under an hour, `h:mm:ss` from an hour (story 4v)."""
    whole = max(0, int(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def printable(text: str) -> str:
    """`text` without control or other non-printable characters (story 4v).

    A state or a line drawn in the bottom toolbar must not carry a newline or
    an escape that would move the cursor or break the drawn area.
    """
    return "".join(character for character in text if character.isprintable())


def cut(text: str, room: int, cells: Callable[[str], int] = len) -> str:
    """`text`, cut with `…` so it takes at most `room` cells; empty with no room."""
    if cells(text) <= room:
        return text
    if room <= 0:
        return ""
    kept: list[str] = []
    used = 0
    for character in text:
        width = max(cells(character), 0)
        if used + width > room - 1:
            break
        kept.append(character)
        used += width
    return "".join(kept) + "…"


def job_line(
    frame: str,
    words: str,
    state: str,
    since: str,
    columns: int,
    cells: Callable[[str], int] = len,
) -> str:
    """One running job's line, at most `columns - 1` cells wide.

    `cells` measures text in terminal cells (the line editor passes the
    library's `get_cwidth`, so wide characters count as two). The state is cut
    to `STATE_CELLS`, then the words to what is left; on a terminal narrower
    than the rest of the line, the whole line is cut. The last column is left
    free: a line that fills the width wraps on some terminals.
    """
    width = columns - 1
    state = cut(printable(state), STATE_CELLS, cells)
    words = printable(words)
    room = width - cells(frame) - cells(state) - cells(since) - 3
    if room >= 1:
        return f"{frame} {cut(words, room, cells)} {state} {since}"
    return cut(f"{frame} {words} {state} {since}", width, cells)


@dataclass(slots=True)
class _Job:
    words: str
    since: float
    state: str = _ACKNOWLEDGED
    arrived: bool = False


class _Jobs:
    """The jobs this session was acknowledged, and the results that have arrived.

    One thread per job waits on the daemon's stream for it, reports each state
    it reads, and hands the result over; it only waits, and never prints
    itself. What a job is — its words, the acknowledgement's monotonic time,
    its last state — and whether a line is being read are kept under one lock,
    so the choice between printing an arrival above the prompt and holding it
    is made under the lock that records whether a read is in progress
    (story 4v). Without a screen — no line editor, or one that failed — every
    result is held for the next prompt, as `4q` printed them.
    """

    def __init__(self, client: DaemonClient, clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._clock = clock
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._waiting: dict[str, threading.Thread] = {}
        self._arrived: list[tuple[str, Followed]] = []
        self._screen: _Screen | None = None
        self._reading = False

    def attach(self, screen: _Screen | None) -> None:
        with self._lock:
            self._screen = screen
            if screen is None:
                self._reading = False

    def follow(self, job_id: str, words: str = "") -> None:
        with self._lock:
            if job_id in self._waiting:
                # The daemon answered with a job already in flight (one job per
                # kind and scope): its result is already on its way here.
                return
            waiter = threading.Thread(
                target=self._wait, args=(job_id,), name=f"pm-ai-follow-{job_id}", daemon=True
            )
            self._waiting[job_id] = waiter
            self._jobs[job_id] = _Job(words or job_id, self._clock())
        waiter.start()

    def waiting(self, job_id: str) -> bool:
        """Whether the job's result has not been printed yet."""
        with self._lock:
            return job_id in self._waiting

    def in_flight(self) -> list[str]:
        """The jobs whose result has not arrived, in the order they were acknowledged."""
        with self._lock:
            return [job_id for job_id, job in self._jobs.items() if not job.arrived]

    def running(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if not job.arrived)

    def deliver(self) -> None:
        """Print every result that has arrived, each line prefixed by its job id.

        A result is forgotten only once it is printed: if printing fails
        partway, that result and the ones after it are held again, for the
        next prompt.
        """
        with self._lock:
            arrived, self._arrived = self._arrived, []
        for done, (job_id, followed) in enumerate(arrived):
            try:
                _print_result(job_id, followed)
            except BaseException:
                with self._lock:
                    self._arrived[:0] = arrived[done:]
                raise
            with self._lock:
                self._waiting.pop(job_id, None)
                self._jobs.pop(job_id, None)

    # ── what the session draws (`editor.Board`) ──────────────────────────────

    def prompt(self, text: str) -> str:
        """`pm-ai> ` with none running; `pm-ai [2 running]> ` while two are."""
        count = self.running()
        if count == 0:
            return text
        return f"{text.removesuffix('> ')} [{count} running]> "

    def lines(self, columns: int, cells: Callable[[str], int] = len) -> list[str]:
        """A line per running job, at most `JOB_LINES`, then `+N more`.

        Each at most `columns - 1` cells, measured by `cells`.
        """
        now = self._clock()
        with self._lock:
            running = [
                (job.words, job.state, job.since) for job in self._jobs.values() if not job.arrived
            ]
        shown = []
        for words, state, since in running[:JOB_LINES]:
            seconds = max(0.0, now - since)
            frame = _SPINNER[int(seconds / REDRAW_SECONDS) % len(_SPINNER)]
            shown.append(job_line(frame, words, state, elapsed(seconds), columns, cells))
        if len(running) > JOB_LINES:
            shown.append(cut(f"+{len(running) - JOB_LINES} more", columns - 1, cells))
        return shown

    def opened(self) -> None:
        """A read has started: print what arrived since the last, then print on arrival."""
        with self._lock:
            if self._screen is None:
                return
            self._reading = True
            if self._arrived:
                self._above()

    def closed(self) -> None:
        with self._lock:
            self._reading = False

    # ── the waiting threads ──────────────────────────────────────────────────

    def _wait(self, job_id: str) -> None:
        followed: Followed
        try:
            followed = self._client.follow(job_id, on_state=lambda state: self._state(job_id, state))
        except BaseException as error:  # noqa: BLE001 — a lost stream, said by name
            # Any way the wait ends, the job arrives: or the count and the
            # redraw would never stop.
            followed = StreamLost(job_id, type(error).__name__)
        with self._lock:
            self._arrived.append((job_id, followed))
            job = self._jobs.get(job_id)
            if job is not None:
                job.arrived = True
            if self._reading:
                self._above()
        self._redraw()

    def _state(self, job_id: str, state: str) -> None:
        """The stream read a state; an error here never ends the wait."""
        try:
            if state in FINISHED:
                # The result follows at once; `done` is never drawn as running.
                return
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job.state = printable(str(state))
            self._redraw()
        except Exception:  # noqa: BLE001, S110 — a failed report never ends the wait
            pass

    def _above(self) -> None:
        """Ask the screen to print the arrivals above the prompt; held if it cannot.

        Called under the lock. Scheduling only: the printing runs on the
        session's own loop, and takes the arrivals then, so a request that
        never runs leaves them for the next prompt.
        """
        screen = self._screen
        if screen is None:
            return
        try:
            screen.above(self.deliver)
        except Exception:  # noqa: BLE001, S110 — held; printed before the next prompt
            pass

    def _redraw(self) -> None:
        with self._lock:
            screen = self._screen
        if screen is None:
            return
        try:
            screen.redraw()
        except Exception:  # noqa: BLE001, S110 — drawing never ends the wait
            pass


def _print_result(job_id: str, followed: Followed) -> None:
    if isinstance(followed, Answer):
        for line in followed.output.splitlines():
            print(f"{job_id}: {line}")
        print(f"{job_id}: exit {followed.code}", flush=True)
    else:
        print(f"pm-ai: {followed.sentence}", file=sys.stderr, flush=True)


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


def _through_daemon(
    words: Sequence[str], client: DaemonClient, jobs: _Jobs, typed: str
) -> None:
    """Send a command's words to the daemon and show what comes back.

    `typed` is the line as it was typed, shown on the job's line (story 4v).
    A `transcript drop` asks its questions here first, and goes with the
    answers as options: the daemon never asks (story 11d, AD-21).
    """
    if tuple(words[:2]) == DROP_WORDS and drop_would_ask(words):
        # Asked before the questions: a line that cannot be sent asks nothing.
        unreached = client.reachable()
        if unreached is not None:
            print(f"pm-ai: {unreached.sentence}", file=sys.stderr, flush=True)
            _status(EXIT_UNEXPECTED)
            return
        try:
            words = ask_drop_here(words)
        except Refusal as refused:
            print(f"pm-ai: {refused}", file=sys.stderr, flush=True)
            _status(EXIT_REFUSAL)
            return
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
        jobs.follow(outcome.job_id, typed)
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
        _through_daemon(words, client, jobs, line.strip())
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
    read: Callable[[str], str] | None = None,
    line_editing: bool = False,
    client: DaemonClient | None = None,
    completion: Completion | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Read lines until `exit`, `quit` or Ctrl-D, and return 0.

    `runner` takes the words of a command and returns its exit code. `client`
    is the daemon's (story 4q); without one every command runs through
    `runner`, as before `4q`. `read` is injectable so the loop is a unit test;
    when it is not given, `line_editing` (which the composition root asks for)
    opens `prompt_toolkit` over `completion` (story 4u), falling back to
    `input` with `readline`, and without it `read` is `input`. `completion`'s
    readers are read again after every command line. A `read` that is the line
    editor's (`_Edited`) shows the jobs (story 4v); `clock` is the monotonic
    clock their elapsed time is read from.
    """
    completion = completion if completion is not None else Completion()
    if read is None:
        read = _line_editor(completion) if line_editing else _input
    jobs = None if client is None else _Jobs(client, clock)
    if jobs is not None and isinstance(read, _Edited):
        read.attach(jobs)
    while True:
        try:
            if jobs is not None:
                # What arrived while a command ran is printed before the
                # prompt is drawn; while a line is read, results are printed
                # above it by the line editor, and never into it.
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
        _after(line, completion)


def _after(line: str, completion: Completion) -> None:
    """After a command line, the next Tab reads the files again (story 4u).

    A command may have enrolled a project, added a connector or set a goal. A
    Ctrl-C landing here forgets too: reading again is never wrong.
    """
    try:
        command = is_a_command_line(line)
    except KeyboardInterrupt:
        command = True
    if command:
        completion.forget()
