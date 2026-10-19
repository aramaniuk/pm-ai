"""Story 4v — jobs report themselves at the prompt.

One test per row of 4v's I/O matrix. The session is the line editor's own
(`editor.session`), driven by `prompt_toolkit`'s pipe input with a dummy
output, through `repl.run` exactly as the composition root runs it. The daemon
is a stand-in client whose job is held until the test releases it, and the
monotonic clock is a stand-in the test advances. What is drawn — the prompt and
the bottom of the terminal — is read from the session itself (its `message`
and `bottom_toolbar`, as plain text); what is printed goes to a captured
standard output.

The rows "Startup" and arrival on a real terminal against a real daemon are
pseudo-terminal tests: `tests/slice/test_repl_startup.py` and
`tests/slice/test_job_arrival_pty.py`.
"""

from __future__ import annotations

import io
import re
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence

import pytest

from test_cli_loopback import _client, _frame, _json, _Reply, _token
from test_cli_loopback import _Daemon as _HttpDaemon
from pm_ai.surfaces.api.app import PROMPT_PATH
from pm_ai.surfaces.cli import loopback, repl
from pm_ai.surfaces.cli.completion import Completion
from pm_ai.surfaces.cli.dispatch import EXIT_OK


PROMPT = repl.PROMPT
WAIT = 10.0


# ── The stand-ins ────────────────────────────────────────────────────────────


class _Clock:
    """A monotonic clock the test advances."""

    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


class _Client:
    """The daemon client, faked: each line answered from a script, each job held.

    `follow` reports `first` states, then waits until the test releases the
    job; `report` sends a state on its stream meanwhile.
    """

    def __init__(self, answers=None, results=None, *, first=None) -> None:
        self.answers = dict(answers or {})
        self.results = dict(results or {})
        self.first = dict(first or {})
        self.sent: list[str] = []
        self.release: dict[str, threading.Event] = {}
        self.following: dict[str, threading.Event] = {}
        self.arrived: dict[str, threading.Event] = {}
        self._on_state: dict[str, Callable[[str], None]] = {}
        for job in self.results:
            self._gates(job)

    def _gates(self, job: str) -> None:
        for gates in (self.release, self.following, self.arrived):
            gates.setdefault(job, threading.Event())

    def send(self, line: str):
        self.sent.append(line)
        answer = self.answers.get(line, loopback.Answer(f"daemon ran {line}\n", EXIT_OK))
        if callable(answer):
            return answer()
        return answer

    def follow(self, job_id: str, on_state=None):
        self._gates(job_id)
        if on_state is not None:
            self._on_state[job_id] = on_state
            for state in self.first.get(job_id, ()):
                on_state(state)
        self.following[job_id].set()
        try:
            assert self.release[job_id].wait(WAIT * 3), f"{job_id} was never released"
            return self.results.get(job_id, loopback.StreamLost(job_id, "no script"))
        finally:
            self.arrived[job_id].set()

    def report(self, job_id: str, state: str) -> None:
        self._on_state[job_id](state)


class _Out(io.StringIO):
    """Standard output and error, one stream, written by two threads."""

    def __init__(self) -> None:
        super().__init__()
        self._guard = threading.Lock()

    def write(self, text: str) -> int:
        with self._guard:
            return super().write(text)

    def text(self) -> str:
        with self._guard:
            return self.getvalue()


def _settled(job: str) -> None:
    """The job's waiting thread has handed its result over and ended."""
    for thread in threading.enumerate():
        if thread.name == f"pm-ai-follow-{job}":
            thread.join(WAIT)
            assert not thread.is_alive(), f"{job}'s waiting thread never ended"


def _until(condition: Callable[[], bool], what: str, timeout: float = WAIT) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


class _Session:
    """`repl.run` over the line editor's session, with a driver on another thread."""

    def __init__(self, pipe, client: _Client, monkeypatch, *, runner=None) -> None:
        from prompt_toolkit.output import DummyOutput

        from pm_ai.surfaces.cli import editor

        self.pipe = pipe
        self.client = client
        self.clock = _Clock()
        self.completion = Completion()
        self.live = editor.session(
            self.completion, keep=repl.is_a_command_line, input=pipe, output=DummyOutput()
        )
        self.read = repl._Edited(self.live)
        self.ran: list[list[str]] = []
        self.runner = runner or self._ran
        self.out = _Out()
        monkeypatch.setattr(sys, "stdout", self.out)
        monkeypatch.setattr(sys, "stderr", self.out)
        self.failure: list[BaseException] = []

    def _ran(self, words: Sequence[str]) -> int:
        self.ran.append(list(words))
        return EXIT_OK

    # ── what the driver sees ────────────────────────────────────────────────

    def reading(self) -> bool:
        """A line is being read, and the session has opened it."""
        return self.live.session.app.is_running and self.live._loop is not None

    def prompt(self) -> str:
        """The prompt as the session draws it: its own `message`, as plain text."""
        from prompt_toolkit.formatted_text import to_plain_text

        return to_plain_text(self.live.session.message)

    def toolbar(self) -> list[str]:
        """The bottom of the terminal as the session draws it, line by line."""
        from prompt_toolkit.formatted_text import to_plain_text

        text = to_plain_text(self.live.session.bottom_toolbar)
        return text.split("\n") if text else []

    def typed(self) -> tuple[str, int]:
        buffer = self.live.session.default_buffer
        return buffer.text, buffer.cursor_position

    # ── what the driver does ────────────────────────────────────────────────

    def wait_reading(self, prompt: str | None = None) -> None:
        _until(
            lambda: self.reading() and (prompt is None or self.prompt() == prompt),
            f"a prompt reading {prompt!r}" if prompt else "a prompt",
        )

    def enter(self, line: str, *, prompt: str | None = None) -> None:
        """Wait for a prompt, then type `line` and Enter."""
        self.wait_reading(prompt)
        self.pipe.send_text(line + "\r")
        _until(lambda: not self.reading() or self.typed()[0] == "", "the line to be accepted")

    def type(self, keys: str) -> None:
        self.wait_reading()
        for key in keys:
            self.pipe.send_text(key)
            time.sleep(0.005)

    def acknowledged(self, job: str) -> None:
        """The job was acknowledged and its stream is being followed."""
        assert self.client.following[job].wait(WAIT), f"{job} was never followed"

    def printed(self, text: str) -> None:
        _until(lambda: text in self.out.text(), f"{text!r} to be printed")

    def run(self, drive: Callable[[_Session], None]) -> int:
        def driver() -> None:
            try:
                drive(self)
            except BaseException as failed:  # noqa: BLE001 — re-raised on the test's thread
                self.failure.append(failed)
                # End the session so the test reports the failure, not a hang.
                for gate in self.client.release.values():
                    gate.set()
                self.pipe.send_text("\x04")

        thread = threading.Thread(target=driver, daemon=True)
        thread.start()
        try:
            code = repl.run(
                self.runner, read=self.read, client=self.client, completion=self.completion,
                clock=self.clock,
            )
        finally:
            thread.join(WAIT)
            for gate in self.client.release.values():
                gate.set()
        if self.failure:
            raise self.failure[0]
        return code


@pytest.fixture
def pipe() -> Iterator[object]:
    from prompt_toolkit.input import create_pipe_input

    with create_pipe_input() as created:
        yield created


def _ack(job: str) -> loopback.Acknowledged:
    return loopback.Acknowledged(job)


JOB = "job_aaaaaaaaaaaaaaaa"
OTHER = "job_bbbbbbbbbbbbbbbb"
_SPIN = f"[{re.escape(repl._SPINNER)}]"


# ── The acceptance criterion ─────────────────────────────────────────────────


def test_a_held_dashboard_is_counted_timed_and_printed_when_released(pipe, monkeypatch):
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("written\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    seen: dict[str, object] = {}

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        s.clock.now += 12
        seen["toolbar"] = s.toolbar()
        client.release[JOB].set()
        # No key is pressed: the result is printed above the prompt on arrival.
        s.printed(f"{JOB}: written\n{JOB}: exit 0\n")
        seen["reading"] = s.reading()
        s.wait_reading(PROMPT)
        s.enter("exit")

    assert session.run(drive) == EXIT_OK
    (line,) = seen["toolbar"]
    assert re.fullmatch(rf"{_SPIN} dashboard acknowledged 0:12", line), line
    assert seen["reading"] is True, "printed while the same line was still being read"


# ── The matrix ───────────────────────────────────────────────────────────────


def test_result_while_idle_is_printed_with_no_enter(pipe, monkeypatch):
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("written\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        client.release[JOB].set()
        s.printed(f"{JOB}: exit 0\n")
        assert s.reading() and s.typed() == ("", 0)
        s.wait_reading(PROMPT)
        s.enter("exit")

    assert session.run(drive) == EXIT_OK
    assert session.out.text().count(f"{JOB}: written\n") == 1


def test_result_while_typing_keeps_the_line_and_the_cursor(pipe, monkeypatch):
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("written\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    seen: dict[str, object] = {}

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        s.type("config sh\x1b[D\x1b[D")  # two left arrows: the cursor inside the word
        _until(lambda: s.typed() == ("config sh", 7), "the half-typed line")
        client.release[JOB].set()
        s.printed(f"{JOB}: exit 0\n")
        _until(lambda: s.prompt() == PROMPT, "the count to drop")
        seen["typed"] = s.typed()
        seen["reading"] = s.reading()
        s.pipe.send_text("\x03")  # Ctrl-C clears it
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    assert seen == {"typed": ("config sh", 7), "reading": True}
    assert session.client.sent == ["dashboard"], "the half-typed line was never sent"


def test_count_reads_two_then_one_after_the_first_result(pipe, monkeypatch):
    client = _Client(
        {"dashboard": _ack(JOB), "dashboard --scope project:alpha": _ack(OTHER)},
        {JOB: loopback.Answer("one\n", EXIT_OK), OTHER: loopback.Answer("two\n", EXIT_OK)},
    )
    session = _Session(pipe, client, monkeypatch)

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.enter("dashboard --scope project:alpha", prompt="pm-ai [1 running]> ")
        s.acknowledged(OTHER)
        s.wait_reading("pm-ai [2 running]> ")
        client.release[JOB].set()
        s.wait_reading("pm-ai [1 running]> ")
        client.release[OTHER].set()
        s.wait_reading(PROMPT)
        s.enter("exit")

    assert session.run(drive) == EXIT_OK


def test_two_jobs_are_listed_by_id_when_the_second_is_acknowledged(pipe, monkeypatch):
    client = _Client(
        {"dashboard": _ack(JOB), "dashboard --scope project:alpha": _ack(OTHER)},
        {JOB: loopback.Answer("one\n", EXIT_OK), OTHER: loopback.Answer("two\n", EXIT_OK)},
    )
    session = _Session(pipe, client, monkeypatch)

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.enter("dashboard --scope project:alpha", prompt="pm-ai [1 running]> ")
        s.printed(f"jobs in flight: {JOB}, {OTHER}\n")
        client.release[JOB].set()
        client.release[OTHER].set()
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK


def _job_line_after(pipe, monkeypatch, line: str, job: str, *, seconds: float, states=()) -> str:
    """The one job line shown after `seconds`, with `states` reported on its stream."""
    client = _Client({line: _ack(job)}, {job: loopback.Answer("done\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    seen: list[str] = []

    def drive(s: _Session) -> None:
        s.enter(line)
        s.acknowledged(job)
        s.wait_reading("pm-ai [1 running]> ")
        for state in states:
            client.report(job, state)
        s.clock.now += seconds
        seen.extend(s.toolbar())
        client.release[job].set()
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    (shown,) = seen
    return shown


def test_progress_shows_the_spinner_the_words_the_state_and_the_time(pipe, monkeypatch):
    shown = _job_line_after(
        pipe, monkeypatch, "dashboard --scope personal", JOB, seconds=12, states=("running",)
    )
    assert re.fullmatch(rf"{_SPIN} dashboard --scope personal running 0:12", shown), shown


def test_a_long_job_reads_hours_minutes_and_seconds(pipe, monkeypatch):
    shown = _job_line_after(
        pipe, monkeypatch, "dashboard", JOB, seconds=3600 + 2 * 60 + 5, states=("running",)
    )
    assert shown.endswith(" running 1:02:05"), shown


def test_queued_is_shown_until_running_arrives(pipe, monkeypatch):
    client = _Client(
        {"dashboard": _ack(JOB)}, {JOB: loopback.Answer("done\n", EXIT_OK)}, first={JOB: ("queued",)}
    )
    session = _Session(pipe, client, monkeypatch)
    seen: list[list[str]] = []

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        seen.append(s.toolbar())
        client.report(JOB, "running")
        seen.append(s.toolbar())
        client.release[JOB].set()
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    assert [lines[0].split()[-2] for lines in seen] == ["queued", "running"]


def test_an_unknown_state_is_shown_as_sent_and_counted_as_running(pipe, monkeypatch):
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("done\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    seen: dict[str, object] = {}

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        client.report(JOB, "paused")
        seen["toolbar"] = s.toolbar()
        seen["prompt"] = s.prompt()
        client.release[JOB].set()
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    assert seen["prompt"] == "pm-ai [1 running]> "
    assert " paused 0:00" in seen["toolbar"][0]


def test_many_jobs_show_three_lines_cut_to_the_width_then_more(pipe, monkeypatch):
    long_id = "p" * 180
    lines = [
        f"dashboard --scope project:{long_id}",
        *(f"dashboard --scope project:p{n}" for n in range(1, 5)),
    ]
    jobs = [f"job_{n:016d}" for n in range(5)]
    client = _Client(
        {line: _ack(job) for line, job in zip(lines, jobs, strict=True)},
        {job: loopback.Answer("done\n", EXIT_OK) for job in jobs},
    )
    session = _Session(pipe, client, monkeypatch)
    seen: list[str] = []

    def drive(s: _Session) -> None:
        for count, (line, job) in enumerate(zip(lines, jobs, strict=True)):
            s.enter(line, prompt=PROMPT if count == 0 else f"pm-ai [{count} running]> ")
            s.acknowledged(job)
        s.wait_reading("pm-ai [5 running]> ")
        seen.extend(s.toolbar())
        for job in jobs:
            client.release[job].set()
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    columns = session.live.session.output.get_size().columns
    assert len(seen) == 4, seen
    first, *rest, more = seen
    assert more == "+2 more"
    assert len(first) <= columns - 1 and "…" in first and first.endswith(" acknowledged 0:00")
    assert first.split()[1] == "dashboard"
    for shown, line in zip(rest, lines[1:3], strict=True):
        assert line in shown and len(shown) <= columns - 1


def test_the_hint_comes_first_and_the_job_line_below_it(pipe, monkeypatch):
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("done\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    seen: list[list[str]] = []

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        seen.append(s.toolbar())
        s.type("transcript drop ")
        _until(lambda: s.typed()[0] == "transcript drop ", "the typed command")
        seen.append(s.toolbar())
        client.release[JOB].set()
        s.pipe.send_text("\x03")
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    alone, together = seen
    assert len(alone) == 1 and " dashboard acknowledged " in alone[0]
    hint, job = together
    assert hint == session.completion.hint("transcript drop ")
    assert job == alone[0]


def _counting_redraws(session: _Session, monkeypatch) -> tuple[list[None], threading.Event]:
    """Count `Live.redraw` calls; the event is set once three have been asked for."""
    redraws: list[None] = []
    three = threading.Event()
    real = session.live.redraw

    def counted() -> None:
        redraws.append(None)
        if len(redraws) >= 3:
            three.set()
        real()

    monkeypatch.setattr(session.live, "redraw", counted)
    return redraws, three


def test_no_jobs_is_the_plain_prompt_no_job_line_and_no_redraw(pipe, monkeypatch):
    # A short interval, so a quiet window of 0.3 s spans thirty of them.
    monkeypatch.setattr(repl, "REDRAW_SECONDS", 0.01)
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("done\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    redraws, three = _counting_redraws(session, monkeypatch)
    seen: dict[str, object] = {}

    def drive(s: _Session) -> None:
        s.wait_reading()
        seen["opened"] = (s.prompt(), s.toolbar(), s.live.refreshing)
        time.sleep(0.3)
        seen["idle"] = len(redraws)
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        seen["running"] = three.wait(WAIT)
        client.release[JOB].set()
        _settled(JOB)
        _until(lambda: not s.live.refreshing, "the redraw task to end")
        settled = len(redraws)
        time.sleep(0.3)
        seen["after"] = len(redraws) - settled
        seen["ended"] = (s.prompt(), s.toolbar())
        s.enter("exit")

    assert session.run(drive) == EXIT_OK
    assert seen["opened"] == (PROMPT, [], False)
    assert seen["idle"] == 0, "redrawn with no job running"
    assert seen["running"] is True, "not redrawn while a job ran"
    assert seen["after"] == 0, "still redrawing after the last job ended"
    assert seen["ended"] == (PROMPT, [])


def test_a_state_change_and_an_arrival_each_ask_for_a_redraw(pipe, monkeypatch):
    # The refresh task never fires within the test: every redraw counted is asked.
    monkeypatch.setattr(repl, "REDRAW_SECONDS", 3600.0)
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("done\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    redraws, _ = _counting_redraws(session, monkeypatch)
    seen: dict[str, int] = {}

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        before = len(redraws)
        client.report(JOB, "running")
        seen["state"] = len(redraws) - before
        before = len(redraws)
        client.release[JOB].set()
        _settled(JOB)
        seen["arrival"] = len(redraws) - before
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    assert seen == {"state": 1, "arrival": 1}


def test_a_finished_state_is_never_drawn_as_running(pipe, monkeypatch):
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("done\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    seen: list[list[str]] = []

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        client.report(JOB, "running")
        for finished in ("done", "failed"):
            client.report(JOB, finished)
            seen.append(s.toolbar())
        client.release[JOB].set()
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    for toolbar in seen:
        (line,) = toolbar
        assert line.split()[-2] == "running", line


def test_the_job_line_shows_the_words_as_typed(pipe, monkeypatch):
    jobs = iter([JOB, OTHER])
    client = _Client({}, {JOB: loopback.Answer("one\n", EXIT_OK), OTHER: loopback.Answer("two\n", EXIT_OK)})

    def acknowledge(line: str) -> loopback.Acknowledged:
        client.sent.append(line)
        return _ack(next(jobs))

    client.send = acknowledge  # type: ignore[method-assign]
    session = _Session(pipe, client, monkeypatch)
    seen: list[str] = []

    def drive(s: _Session) -> None:
        s.enter("dashboard --scope 'personal'")
        s.acknowledged(JOB)
        s.enter("transcript drop ./a.vtt --scope personal", prompt="pm-ai [1 running]> ")
        s.acknowledged(OTHER)
        s.wait_reading("pm-ai [2 running]> ")
        seen.extend(s.toolbar())
        client.release[JOB].set()
        client.release[OTHER].set()
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    quoted, path = seen
    assert " dashboard --scope 'personal' acknowledged " in quoted, quoted
    assert " transcript drop ./a.vtt --scope personal acknowledged " in path, path
    # What was sent is the parsed, absolutised line, which is not what is shown.
    assert client.sent[0] == "dashboard --scope personal"
    assert "./a.vtt" not in client.sent[1]


# ── Printing above the prompt, when it cannot ────────────────────────────────


def _live(pipe):
    from prompt_toolkit.output import DummyOutput

    from pm_ai.surfaces.cli import editor

    return editor.session(Completion(), keep=repl.is_a_command_line, input=pipe, output=DummyOutput())


def test_above_with_no_read_running_refuses_and_the_jobs_hold_it(pipe, monkeypatch, capsys):
    live = _live(pipe)
    with pytest.raises(RuntimeError):
        live.above(lambda: None)
    client = _Client({}, {JOB: loopback.Answer("written\n", EXIT_OK)})
    jobs = repl._Jobs(client)
    repl._Edited(live).attach(jobs)
    jobs.opened()  # a read recorded as open, its loop not yet there
    jobs.follow(JOB, "dashboard")
    client.release[JOB].set()
    _settled(JOB)
    assert capsys.readouterr().out == "", "printed with no read to print above"
    jobs.deliver()
    jobs.deliver()
    assert capsys.readouterr().out.count(f"{JOB}: written\n{JOB}: exit 0\n") == 1


def test_above_on_a_closed_loop_refuses(pipe):
    import asyncio

    live = _live(pipe)
    closed = asyncio.new_event_loop()
    closed.close()
    live._loop = closed
    with pytest.raises(RuntimeError):
        live.above(lambda: None)


def test_a_callback_landing_after_the_read_ended_prints_nothing(pipe):
    live = _live(pipe)
    written: list[str] = []
    live._above(lambda: written.append("printed"))
    assert written == [], "printed with the application not running"


@pytest.mark.parametrize("lands", ["refused", "after the read"])
def test_an_arrival_that_cannot_print_above_is_printed_once_before_the_next_prompt(
    pipe, monkeypatch, lands
):
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("written\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    if lands == "refused":
        def above(write):  # the loop closed between the check and the call
            raise RuntimeError("Event loop is closed")
    else:
        def above(write):  # the callback lands once the line was accepted: nothing printed
            return None

    monkeypatch.setattr(session.live, "above", above)
    seen: dict[str, str] = {}

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        client.release[JOB].set()
        _settled(JOB)
        seen["while reading"] = s.out.text()
        s.enter("doctor", prompt=PROMPT)
        _until(lambda: s.ran == [["doctor"]], "doctor to run")
        s.wait_reading(PROMPT)
        seen["at the next prompt"] = s.out.text()
        s.enter("exit")

    assert session.run(drive) == EXIT_OK
    assert f"{JOB}: written" not in seen["while reading"]
    out = seen["at the next prompt"]
    assert out.count(f"{JOB}: written\n{JOB}: exit 0\n") == 1
    assert out.index("exit 0\n") < out.index(f"{JOB}: written"), "after doctor's status"
    assert session.out.text().count(f"{JOB}: written") == 1


def test_a_result_during_a_local_command_is_held_until_it_returns(pipe, monkeypatch):
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("written\n", EXIT_OK)})

    def runner(words: Sequence[str]) -> int:
        # `goal set` asking its questions when the result arrives.
        print("Goal title: ", end="", flush=True)
        client.release[JOB].set()
        _settled(JOB)
        print("answered", flush=True)
        return EXIT_OK

    session = _Session(pipe, client, monkeypatch, runner=runner)
    seen: dict[str, str] = {}

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.enter("goal set", prompt="pm-ai [1 running]> ")
        s.wait_reading(PROMPT)
        seen["at the next prompt"] = s.out.text()
        s.enter("exit")

    assert session.run(drive) == EXIT_OK
    out = seen["at the next prompt"]
    assert "Goal title: answered\nexit 0\n" in out, out
    assert out.index("exit 0\n", out.index("answered")) < out.index(f"{JOB}: written")
    assert f"{JOB}: exit 0\n" in out, "printed before the next prompt opened"


def test_a_result_during_an_inline_wait_is_printed_before_the_next_prompt(pipe, monkeypatch):
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("written\n", EXIT_OK)})

    def config_show() -> loopback.Answer:
        client.release[JOB].set()
        _settled(JOB)
        return loopback.Answer("the settings\n", EXIT_OK)

    client.answers["config show"] = config_show
    session = _Session(pipe, client, monkeypatch)
    seen: dict[str, str] = {}

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.enter("config show", prompt="pm-ai [1 running]> ")
        s.wait_reading(PROMPT)
        seen["out"] = s.out.text()
        s.enter("exit")

    assert session.run(drive) == EXIT_OK
    out = seen["out"]
    assert "the settings\nexit 0\n" in out
    assert out.index("the settings") < out.index(f"{JOB}: written") < out.index(f"{JOB}: exit 0")


def test_ctrl_c_while_typing_clears_the_line_and_the_job_stays(pipe, monkeypatch):
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("done\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    seen: dict[str, object] = {}

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        s.type("doc")
        _until(lambda: s.typed()[0] == "doc", "the half-typed line")
        s.pipe.send_text("\x03")
        _until(lambda: s.reading() and s.typed()[0] == "", "a fresh line")
        seen["prompt"] = s.prompt()
        seen["toolbar"] = s.toolbar()
        seen["arrived"] = client.arrived[JOB].is_set()
        client.release[JOB].set()
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    assert seen["prompt"] == "pm-ai [1 running]> "
    assert len(seen["toolbar"]) == 1 and " dashboard " in seen["toolbar"][0]
    assert seen["arrived"] is False
    assert session.ran == [] and client.sent == ["dashboard"]


@pytest.fixture
def daemon() -> Iterator[_HttpDaemon]:
    """`test_cli_loopback`'s stand-in daemon over real HTTP on loopback."""
    stand_in = _HttpDaemon()
    try:
        yield stand_in
    finally:
        stand_in.close()


def test_a_state_report_that_raises_never_ends_the_wait(pipe, monkeypatch, daemon, tmp_path):
    """The real client over HTTP; the prompt's state callback raises on every event."""
    daemon.replies[("POST", PROMPT_PATH)] = _json(202, {"inline": False, "job_id": "job_ab"})
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(
        frames=[_frame("queued"), 0.2, _frame("running"), 0.2,
                _frame("done", {"output": "written\n", "exit": 0})]
    )
    real = _client(_token(tmp_path / "t"), daemon.port)

    def raises(self, job_id: str, state: str) -> None:
        raise RuntimeError("the report broke")

    monkeypatch.setattr(repl._Jobs, "_state", raises)
    client = _Client()
    client.send = real.send  # type: ignore[method-assign]
    followed = threading.Event()

    def follow(job_id: str, on_state=None):
        try:
            return real.follow(job_id, on_state)
        finally:
            followed.set()

    client.follow = follow  # type: ignore[method-assign]
    session = _Session(pipe, client, monkeypatch)

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        assert followed.wait(WAIT)
        s.printed("job_ab: written\njob_ab: exit 0\n")
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK


def test_the_client_reports_each_state_in_order_and_survives_a_raising_callback(daemon, tmp_path):
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(
        frames=[_frame("queued"), _frame("running"), _frame("done", {"output": "ok\n", "exit": 0})]
    )
    reported: list[str] = []

    def on_state(state: str) -> None:
        reported.append(state)
        raise RuntimeError("the report broke")

    followed = _client(_token(tmp_path / "t"), daemon.port).follow("job_ab", on_state)
    assert followed == loopback.Answer("ok\n", 0)
    assert reported == ["queued", "running", "done"]


def test_a_failed_job_is_its_sentence_and_exit_1_on_arrival(pipe, monkeypatch):
    failed = loopback._result(
        JOB, {"state": "failed", "result": {"error": "the claim on meetings/ is held", "type": "ArtifactBusy"}}
    )
    client = _Client({"dashboard": _ack(JOB)}, {JOB: failed})
    session = _Session(pipe, client, monkeypatch)

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        client.release[JOB].set()
        s.printed(f"{JOB}: pm-ai: the claim on meetings/ is held\n{JOB}: exit 1\n")
        assert s.reading()
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK


def test_a_lost_stream_is_one_sentence_on_arrival_and_the_count_drops(pipe, monkeypatch):
    lost = loopback.StreamLost(JOB, "the daemon closed its stream before the job finished")
    client = _Client({"dashboard": _ack(JOB)}, {JOB: lost})
    session = _Session(pipe, client, monkeypatch)

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        client.release[JOB].set()
        s.printed(lost.sentence)
        s.wait_reading(PROMPT)
        assert s.toolbar() == []
        s.enter("doctor")
        s.printed("exit 0\n")
        s.enter("exit", prompt=PROMPT)

    assert session.run(drive) == EXIT_OK
    assert session.out.text().count(JOB) == 2, "named at the acknowledgement and once when lost"
    assert session.ran == [["doctor"]], "the session continued"


def test_exit_prints_the_arrived_result_and_names_the_running_job(pipe, monkeypatch):
    client = _Client(
        {"dashboard": _ack(JOB), "dashboard --scope project:alpha": _ack(OTHER)},
        {JOB: loopback.Answer("one\n", EXIT_OK), OTHER: loopback.Answer("two\n", EXIT_OK)},
    )
    session = _Session(pipe, client, monkeypatch)

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.enter("dashboard --scope project:alpha", prompt="pm-ai [1 running]> ")
        s.acknowledged(OTHER)
        client.release[JOB].set()
        s.enter("exit", prompt="pm-ai [1 running]> ")

    assert session.run(drive) == EXIT_OK
    out = session.out.text()
    assert out.count(f"{JOB}: one\n{JOB}: exit 0\n") == 1
    assert f"{OTHER} is still running; it continues in the daemon" in out
    assert f"{JOB} is still running" not in out
    assert f"{OTHER}: two" not in out, "a result after the ending is not shown"


def test_startup_opens_with_the_plain_prompt_and_what_4u_drew(pipe, monkeypatch):
    """The 1.0 s budget is `tests/slice/test_repl_startup.py`'s; here, what is drawn at open."""
    client = _Client()
    session = _Session(pipe, client, monkeypatch)
    seen: dict[str, object] = {}

    def drive(s: _Session) -> None:
        s.wait_reading()
        seen["drawn"] = (s.prompt(), s.toolbar())
        s.enter("exit")

    assert session.run(drive) == EXIT_OK
    assert seen["drawn"] == (PROMPT, [])
    assert client.sent == []
    assert not any(t.name.startswith("pm-ai-follow-") for t in threading.enumerate())


def _without_prompt_toolkit(monkeypatch) -> None:
    from pm_ai.surfaces import cli

    monkeypatch.setitem(sys.modules, "prompt_toolkit", None)
    monkeypatch.delitem(sys.modules, "pm_ai.surfaces.cli.editor", raising=False)
    monkeypatch.delattr(cli, "editor", raising=False)


def _results_at_the_next_prompt(monkeypatch, capsys, **run) -> None:
    """`4q`'s loop: the prompt is always `pm-ai> `, and a result waits for Enter."""
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("written\n", EXIT_OK)})
    prompts: list[str] = []

    def typing() -> str:
        client.release[JOB].set()
        _settled(JOB)
        print("<typed>", end="", flush=True)
        return "doctor"

    steps: list[object] = ["dashboard", typing, "exit"]

    def read(prompt: str) -> str:
        prompts.append(prompt)
        print(prompt, end="", flush=True)
        step = steps.pop(0)
        return step() if callable(step) else step  # type: ignore[operator, return-value]

    monkeypatch.setattr(repl, "_input", read)
    ran: list[list[str]] = []
    code = repl.run(lambda words: ran.append(list(words)) or EXIT_OK, client=client, **run)
    assert code == EXIT_OK
    assert prompts == [PROMPT] * 3
    out = capsys.readouterr().out
    assert out.index("<typed>") < out.index(f"{JOB}: written")
    assert ran == [["doctor"]]


def test_without_the_runtime_extra_results_wait_for_the_next_prompt(monkeypatch, capsys):
    _without_prompt_toolkit(monkeypatch)
    _results_at_the_next_prompt(monkeypatch, capsys, line_editing=True)


def test_when_the_terminal_check_fails_results_wait_for_the_next_prompt(monkeypatch, capsys):
    monkeypatch.setattr(repl, "_is_a_terminal", lambda stream: False)
    _results_at_the_next_prompt(monkeypatch, capsys, line_editing=True)


def test_when_the_line_editor_fails_mid_session_results_wait_for_the_next_prompt(
    pipe, monkeypatch
):
    """The read fails with a result held and the redraw task live: printed once, before `4q`'s prompt."""
    client = _Client({"dashboard": _ack(JOB)}, {JOB: loopback.Answer("written\n", EXIT_OK)})
    session = _Session(pipe, client, monkeypatch)
    real = session.live.session.prompt
    reads: list[None] = []

    def breaks(*args: object, **kwargs: object) -> str:
        reads.append(None)
        line = real(*args, **kwargs)
        if len(reads) == 2:
            raise RuntimeError("the library broke")
        return line

    monkeypatch.setattr(session.live.session, "prompt", breaks)

    def held(write):
        raise RuntimeError("Event loop is closed")

    monkeypatch.setattr(session.live, "above", held)
    prompts: list[str] = []

    def fallback(prompt: str) -> str:
        prompts.append(prompt)
        print("<4q>" + prompt, end="", flush=True)
        return "exit"

    monkeypatch.setattr(repl, "_input", fallback)

    def drive(s: _Session) -> None:
        s.enter("dashboard")
        s.acknowledged(JOB)
        s.wait_reading("pm-ai [1 running]> ")
        _until(lambda: s.live.refreshing, "the redraw task")
        client.release[JOB].set()
        _settled(JOB)
        assert f"{JOB}: written" not in s.out.text()
        s.pipe.send_text("x\r")

    assert session.run(drive) == EXIT_OK
    out = session.out.text()
    assert repl.line_editor_failed(RuntimeError(), started=True) in out
    assert out.count(f"{JOB}: written\n{JOB}: exit 0\n") == 1
    assert out.index(f"{JOB}: exit 0") < out.index("<4q>")
    assert prompts == [PROMPT]


# ── The pieces ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [(0, "0:00"), (12, "0:12"), (59.9, "0:59"), (60, "1:00"), (3599, "59:59"), (3600, "1:00:00"),
     (3725, "1:02:05"), (-3, "0:00")],
)
def test_elapsed_is_minutes_then_hours_from_an_hour(seconds, shown):
    assert repl.elapsed(seconds) == shown


def test_a_job_line_fits_its_width():
    line = repl.job_line("⠋", "x" * 200, "running", "0:12", 40)
    assert len(line) == 39 and line.endswith("… running 0:12")
    assert repl.job_line("⠋", "dashboard", "running", "0:12", 80) == "⠋ dashboard running 0:12"


def _cells(text: str) -> int:
    from prompt_toolkit.utils import get_cwidth

    return get_cwidth(text)


@pytest.mark.parametrize("columns", [80, 40, 21, 20])
def test_wide_characters_are_measured_in_cells(columns):
    line = repl.job_line("⠋", "会議 🚀" * 40, "running", "0:12", columns, _cells)
    assert _cells(line) <= columns - 1, line
    assert line.endswith(" running 0:12") and "…" in line


def test_a_long_state_is_cut_too():
    line = repl.job_line("⠋", "dashboard", "s" * 200, "0:12", 80, _cells)
    state = line.split()[-2]
    assert _cells(state) == repl.STATE_CELLS and state.endswith("…")
    assert "dashboard" in line and _cells(line) <= 79


@pytest.mark.parametrize("columns", [1, 2, 5, 12])
def test_a_terminal_narrower_than_the_line_cuts_the_whole_line(columns):
    line = repl.job_line("⠋", "dashboard", "running", "1:02:05", columns, _cells)
    assert _cells(line) <= max(columns - 1, 0), line


def test_a_state_with_control_characters_cannot_break_the_toolbar():
    line = repl.job_line("⠋", "dash\tboard", "run\nning\x1b[2J", "0:12", 80, _cells)
    assert not any(not character.isprintable() for character in line), repr(line)
    assert line == "⠋ dashboard running[2J 0:12"
    client = _Client({}, {JOB: loopback.Answer("x\n", EXIT_OK)})
    jobs = repl._Jobs(client)
    jobs.follow(JOB, "dashboard")
    client.following[JOB].wait(WAIT)
    client.report(JOB, "pau\x1bsed\n")
    (shown,) = jobs.lines(80, _cells)
    assert shown.endswith(" dashboard paused 0:00"), repr(shown)
    client.release[JOB].set()
    _settled(JOB)


def test_a_wait_ended_by_any_exception_still_arrives(capsys):
    class _Stopped(BaseException):
        pass

    class _Raising(_Client):
        def follow(self, job_id, on_state=None):
            raise _Stopped

    jobs = repl._Jobs(_Raising())
    jobs.follow(JOB, "dashboard")
    _settled(JOB)
    assert jobs.running() == 0 and jobs.prompt(PROMPT) == PROMPT
    jobs.deliver()
    assert JOB in capsys.readouterr().err


def test_a_result_that_fails_to_print_is_held_again(monkeypatch, capsys):
    client = _Client({}, {JOB: loopback.Answer("one\n", EXIT_OK), OTHER: loopback.Answer("two\n", EXIT_OK)})
    jobs = repl._Jobs(client)
    for job in (JOB, OTHER):
        jobs.follow(job, "dashboard")
        client.release[job].set()
        _settled(job)
    real = repl._print_result
    broken = [True]

    def flaky(job_id, followed):
        if job_id == OTHER and broken:
            broken.clear()
            raise BrokenPipeError
        real(job_id, followed)

    monkeypatch.setattr(repl, "_print_result", flaky)
    with pytest.raises(BrokenPipeError):
        jobs.deliver()
    assert jobs.waiting(OTHER) and not jobs.waiting(JOB)
    jobs.deliver()
    out = capsys.readouterr().out
    assert out.count(f"{JOB}: one\n") == 1 and out.count(f"{OTHER}: two\n") == 1
