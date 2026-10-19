"""Story 4v end to end: a job's result arrives at a real terminal without a key.

The console script runs on a pseudo-terminal (`test_repl_startup._Terminal`:
a temporary home, the cursor-position request off) against a real daemon
(`pm-ai daemon run`, `test_daemon_requests_slice.Home`) on a free port in the
same temporary home. A real job finishes in a second or two, so only what a
real daemon makes certain is asserted: the result arrives with no key pressed,
and a line typed but not entered is still on the line after it. The running
prompt itself is asserted with a held stand-in, in
`tests/surfaces/test_cli_job_progress.py`.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from test_daemon_requests_slice import Home, _free_port, _wait_listening
from test_repl_startup import _Terminal

ACKNOWLEDGED = re.compile(rb"(job_[0-9a-f]+): acknowledged")
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")


@pytest.fixture
def home(tmp_path: Path) -> Home:
    root = tmp_path / "home"
    (root / ".pm-ai").mkdir(parents=True)
    port = _free_port()
    (root / ".pm-ai" / "config.toml").write_text(
        f'daemon_port = {port}\ndisplay_timezone = "UTC"\n'
    )
    return Home(root, port)


@pytest.fixture
def daemon_process(home: Home) -> Iterator[subprocess.Popen[str]]:
    process = home.start()
    try:
        _wait_listening(process)
        yield process
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def _acknowledged(session: _Terminal, count: int) -> bytes:
    assert session.read_until_text(b": acknowledged", count), session.output
    return ACKNOWLEDGED.findall(session.output)[count - 1]


def test_a_result_arrives_with_no_key_and_a_typed_line_survives_it(home, daemon_process):
    session = _Terminal(home.root)
    try:
        assert session.read_until(1), session.output
        session.type(b"dashboard\r")
        first = _acknowledged(session, 1)
        # Nothing else is typed: the result and its status arrive on their own.
        assert session.read_until_text(first + b": exit 0", 1), session.output
        assert first + b": the personal dashboard is written: " in session.output

        # `config sh` arrives with the Enter that sends `dashboard`, so it is on
        # the next line before that job's result can arrive.
        session.type(b"dashboard\rconfig sh")
        second = _acknowledged(session, 2)
        assert second != first
        assert session.read_until_text(second + b": exit 0", 1), session.output
        # The prompt is drawn again below the result, `config sh` still on it.
        def after() -> bytes:
            shown = session.output[session.output.index(second + b": exit 0") :]
            return ANSI.sub(b"", shown)

        deadline = time.monotonic() + 5
        while b"config sh" not in after() and time.monotonic() < deadline:
            session._drain(0.05)
        assert b"config sh" in after(), after()
        assert b"pm-ai> " in after(), after()

        session.type(b"\x03")  # Ctrl-C: the half-typed line is cleared, never sent
        session.type(b"exit\r")
        assert session.finish() == 0
    finally:
        session.close()
    text = session.output.decode(errors="replace")
    assert "line editor" not in text, text
