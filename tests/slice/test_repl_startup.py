"""Story 4f — the real `pm-ai` under a pseudo-terminal, and what it imports.

The console script is started on a pseudo-terminal and the arrival of
`pm-ai> ` is timed from just before the process starts; the elapsed figure is
in the failure message and the 1.0 s bound is asserted. When this slice was
built one run took 0.39 s (2026-10-13, an x86_64 interpreter under Rosetta 2);
story 7 re-baselines.
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
SERVER_MODULES = ("fastapi", "uvicorn", "msal")


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


def test_a_command_then_recall_then_exit_writes_no_event_log_line(terminal):
    """`config show` with encryption off composes, writes no protected file,
    and so leaves the event log and the memory tree as they were (`1p`)."""
    session, home = terminal
    assert session.read_until(1), f"no prompt; printed {session.output!r}"
    session.type(b"config show\n")
    assert session.read_until(2), session.output
    # Up arrow then Enter: the previous line again, through `readline`.
    session.type(b"\x1b[A\n")
    assert session.read_until(3), session.output
    session.type(b"exit\n")
    assert session.finish() == 0
    text = session.output.decode(errors="replace").replace("\r\n", "\n")
    assert text.count("daemon_port") == 2, text
    assert text.splitlines().count("exit 0") == 2, text
    memory = home / ".pm-ai" / "memory"
    assert not memory.exists() or not any(memory.rglob("*")), sorted(memory.rglob("*"))
    created = sorted(p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file())
    # The store, and since story 4r the empty sidecar of the claim it was
    # prepared under (AD-5) — structure beside it, never an artifact.
    assert created == [
        ".pm-ai/private/operational.db",
        ".pm-ai/private/operational.db.lock",
    ], created


def test_the_console_script_module_imports_no_server_module():
    """`fastapi`, `uvicorn` and `msal` stay off the path to the prompt (binds `4e`)."""
    probe = (
        "import sys, pm_ai.app.entry\n"
        f"print(','.join(m for m in {SERVER_MODULES!r} if m in sys.modules))\n"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert loaded == "", f"importing the console script's module loaded {loaded}"
