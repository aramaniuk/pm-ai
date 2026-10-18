"""Stories 4f and 4u — the real `pm-ai` under a pseudo-terminal, and what it imports.

The console script is started on a pseudo-terminal and the arrival of
`pm-ai> ` is timed from just before the process starts; the elapsed figure is
in the failure message and the 1.0 s bound is asserted. When `4f` was built one
run took 0.39 s (2026-10-13, an x86_64 interpreter under Rosetta 2). With
`prompt_toolkit` loaded at open (story 4u) the median of seven runs was 0.71 s
(2026-10-18, same interpreter; 0.43 s with the library's import made to fail,
so the library costs about 0.28 s); story 7 re-baselines.

Since story 4u the prompt is drawn by `prompt_toolkit`, which draws it again
when a line is accepted, so the tests that type wait on the status lines a
command prints rather than on a count of prompts.
"""

from __future__ import annotations

import os
import pty
import select
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

PROMPT = b"pm-ai> "
BUDGET_SECONDS = 1.0
CONSOLE_SCRIPT = Path(sys.executable).parent / "pm-ai"
SERVER_MODULES = ("fastapi", "uvicorn", "msal", "prompt_toolkit")
"""Modules the console script's module must not import. `prompt_toolkit` (4u)
is imported when the prompt opens, never by `pm-ai <command>`."""
CURSOR_POSITION_REQUEST = b"\x1b[6n"


class _Terminal:
    """`pm-ai` with no arguments on a pseudo-terminal, and what it printed."""

    def __init__(self, home: Path) -> None:
        env = dict(
            os.environ, HOME=str(home), PM_AI_DISABLE_ENCRYPTION="1", TERM="xterm"
        )
        # `Popen` on a pty pair rather than `pty.fork`: forking a pytest
        # process that may hold threads is a deadlock risk in the child.
        self.fd, follower = pty.openpty()
        self.started = time.monotonic()
        self.process = subprocess.Popen(
            [str(CONSOLE_SCRIPT)],
            stdin=follower,
            stdout=follower,
            stderr=follower,
            env=env,
            cwd=home,
            start_new_session=True,
        )
        os.close(follower)
        self.output = b""
        self.closed = False

    def read_until(self, count: int, timeout: float = 30.0) -> bool:
        """Read until `PROMPT` has appeared `count` times, or time runs out."""
        deadline = time.monotonic() + timeout
        while self.output.count(PROMPT) < count and time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(self.fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                self.output += chunk
        return self.output.count(PROMPT) >= count

    def read_until_text(self, text: bytes, count: int, timeout: float = 30.0) -> bool:
        """Read until `text` has appeared `count` times, or time runs out."""
        deadline = time.monotonic() + timeout
        while self.output.count(text) < count and time.monotonic() < deadline:
            self._drain(0.05)
        return self.output.count(text) >= count

    def type(self, line: bytes) -> None:
        os.write(self.fd, line)

    def finish(self, timeout: float = 30.0) -> int:
        deadline = time.monotonic() + timeout
        while self.process.poll() is None and time.monotonic() < deadline:
            self._drain(0.05)
        if self.process.poll() is None:
            raise AssertionError(f"pm-ai did not exit; it printed {self.output!r}")
        self._drain(0.2)
        return self.process.returncode

    def close(self) -> None:
        """Kill a child still running and close the terminal, once."""
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        if not self.closed:
            os.close(self.fd)
            self.closed = True

    def _drain(self, wait: float) -> None:
        ready, _, _ = select.select([self.fd], [], [], wait)
        if ready:
            try:
                self.output += os.read(self.fd, 4096)
            except OSError:
                pass


@pytest.fixture
def terminal(tmp_path) -> Iterator[tuple[_Terminal, Path]]:
    if not CONSOLE_SCRIPT.exists():
        pytest.fail(
            f"no `pm-ai` console script at {CONSOLE_SCRIPT}; install it with "
            f"`uv sync --extra runtime`"
        )
    home = tmp_path / "home"
    home.mkdir()
    session = _Terminal(home)
    try:
        yield session, home
    finally:
        session.close()


def test_the_prompt_shows_within_one_second(terminal):
    session, home = terminal
    arrived = session.read_until(1)
    elapsed = time.monotonic() - session.started
    assert arrived, f"no prompt after {elapsed:.3f} s; printed {session.output!r}"
    assert elapsed <= BUDGET_SECONDS, (
        f"the prompt took {elapsed:.3f} s, over the {BUDGET_SECONDS} s budget"
    )
    session.type(b"exit\n")
    assert session.finish() == 0
    # Nothing was composed to open it: the home is as it was.
    assert list(home.iterdir()) == []
    # Story 4u: the cursor-position request is off, so it is never sent and
    # the library never warns that a terminal did not answer it.
    assert CURSOR_POSITION_REQUEST not in session.output, session.output
    assert b"CPR" not in session.output, session.output


def test_a_command_then_recall_then_exit_writes_nothing(terminal):
    """`config show` with no daemon running, typed and then recalled.

    Since story 4q the line goes to the daemon, so with none running each try
    is the one no-daemon sentence and `exit 1` — and the prompt's process
    composes nothing to say so: the home is as it was (`4f`, `4q`).
    """
    session, home = terminal
    assert session.read_until(1), f"no prompt; printed {session.output!r}"
    session.type(b"config show\n")
    assert session.read_until_text(b"exit 1\r\n", 1), session.output
    # Up arrow then Enter: the previous line again, through `prompt_toolkit`
    # (story 4u; `readline` without the runtime extra).
    session.type(b"\x1b[A\n")
    assert session.read_until_text(b"exit 1\r\n", 2), session.output
    session.type(b"exit\n")
    assert session.finish() == 0
    text = session.output.decode(errors="replace").replace("\r\n", "\n")
    assert text.count("`pm-ai daemon run`") == 2, text
    assert text.splitlines().count("exit 1") == 2, text
    assert list(home.iterdir()) == [], sorted(home.rglob("*"))


def test_the_console_script_module_imports_no_server_module():
    """`fastapi`, `uvicorn` and `msal` stay off the path to the prompt (binds `4e`),
    and `prompt_toolkit` off every command's path (binds `4u`)."""
    probe = (
        "import sys, pm_ai.app.entry\n"
        f"print(','.join(m for m in {SERVER_MODULES!r} if m in sys.modules))\n"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert loaded == "", f"importing the console script's module loaded {loaded}"


def test_tab_after_scope_offers_both_projects_and_personal_and_asks_no_daemon(tmp_path):
    """Story 4u's acceptance: two projects enrolled, `dashboard --scope ` and Tab.

    Something listens on the configured `daemon_port` and records whether
    anything connected: completion reads local files and nothing else.
    """
    import socket

    from pm_ai.core.project_registry import ProjectEntry, render_registry

    home = tmp_path / "home"
    (home / ".pm-ai").mkdir(parents=True)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    (home / ".pm-ai" / "config.toml").write_text(
        f'daemon_port = {port}\ndisplay_timezone = "UTC"\n'
    )
    (home / ".pm-ai" / "projects.toml").write_bytes(
        render_registry(
            {pid: ProjectEntry(path=tmp_path / pid) for pid in ("alpha", "beta")}
        )
    )
    before = sorted(home.rglob("*"))
    session = _Terminal(home)
    try:
        assert session.read_until(1), session.output
        session.type(b"dashboard --scope ")
        time.sleep(0.5)
        session.type(b"\t")
        assert session.read_until_text(b"project:beta", 1, timeout=10), session.output
        session.type(b"\x15exit\n")
        assert session.finish() == 0
    finally:
        session.close()
    try:
        connection, _ = listener.accept()
    except BlockingIOError:
        connection = None
    finally:
        listener.close()
    assert connection is None, "completion reached the daemon's port"
    for offered in (b"personal", b"project:alpha", b"project:beta"):
        assert offered in session.output, session.output
    assert sorted(home.rglob("*")) == before


def test_the_hint_line_is_drawn_while_a_command_is_typed(terminal):
    """Story 4u: the bottom line shows the usage of the command being typed.

    Drawn only because the output reports one row below the cursor; with the
    cursor-position request off, the library would otherwise never draw it.
    The words arrive in one write, so the line is drawn whole rather than as
    a difference from an earlier hint.
    """
    session, _ = terminal
    assert session.read_until(1), session.output
    session.type(b"transcript drop ")
    usage = b"transcript drop <file> --scope <scope>"
    assert session.read_until_text(usage, 1, timeout=10), session.output
    session.type(b"\x15exit\n")
    assert session.finish() == 0
