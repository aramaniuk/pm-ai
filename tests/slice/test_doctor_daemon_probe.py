"""`pm-ai doctor`'s `daemon` probe — story 4e's doctor rows, against stand-in servers.

Each stand-in is a real listener on a loopback port, because the probe's whole
job is what comes back over a socket. The "up" row against the real daemon is
in `test_daemon_slice.py`; here the answer is scripted so every state is
reachable on purpose.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pm_ai.domain.health import Health
from pm_ai.platform.doctor import DAEMON_WAIT_SECONDS, daemon_reachable

TOKEN = "s" * 43


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def _answering(status: int, *, pm_ai: bool, body: bytes = b"{}") -> Iterator[tuple[int, list]]:
    """A loopback HTTP server answering every GET with `status`; yields its port and the headers seen."""
    seen: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(dict(self.headers))
            self.send_response(status)
            if pm_ai:
                self.send_header("x-pm-ai", "1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "api_token"
    path.write_text(TOKEN)
    path.chmod(0o600)
    return path


def test_the_wait_is_two_seconds():
    assert DAEMON_WAIT_SECONDS == 2.0


def test_nothing_listening_is_ok_not_running(token_file):
    port = _free_port()
    probe = daemon_reachable(port, token_file)
    assert probe.name == "daemon"
    assert probe.health is Health.OK
    assert probe.detail.startswith("not running")
    assert f"127.0.0.1:{port}" in probe.detail


def test_a_pm_ai_daemon_answering_is_ok_running_with_its_start_time(token_file):
    body = json.dumps(
        {"uptime_seconds": 42, "started_at": "2026-10-12T08:00:00+00:00", "healthy": True, "probes": []}
    ).encode()
    with _answering(200, pm_ai=True, body=body) as (port, seen):
        probe = daemon_reachable(port, token_file)
    assert probe.health is Health.OK
    assert probe.detail.startswith("running")
    assert f"127.0.0.1:{port}" in probe.detail
    assert "2026-10-12T08:00:00+00:00" in probe.detail
    assert "42s" in probe.detail
    # The token is sent, and never printed.
    assert seen[0]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(probe)


def test_something_that_is_not_pm_ai_is_a_warning(token_file):
    with _answering(200, pm_ai=False) as (port, _):
        probe = daemon_reachable(port, token_file)
    assert probe.health is Health.WARNING
    assert probe.detail.startswith("something that is not pm-ai answers")
    assert "daemon_port" in probe.remediation


def test_a_pm_ai_daemon_rejecting_this_token_is_a_warning(token_file):
    with _answering(401, pm_ai=True) as (port, _):
        probe = daemon_reachable(port, token_file)
    assert probe.health is Health.WARNING
    assert "a pm-ai daemon answers" in probe.detail
    assert "rejects this token file" in probe.detail
    assert TOKEN not in str(probe)


def test_a_port_that_accepts_and_never_answers_is_not_answering(token_file):
    """The kernel accepts into the backlog; nothing ever reads or replies."""
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(8)
    try:
        probe = daemon_reachable(silent.getsockname()[1], token_file, timeout=0.3)
    finally:
        silent.close()
    assert probe.health is Health.WARNING
    assert probe.detail.startswith("not answering")


def test_an_absent_token_file_is_named_and_the_question_still_asked(tmp_path):
    missing = tmp_path / "private" / "api_token"
    port = _free_port()
    probe = daemon_reachable(port, missing)
    assert probe.health is Health.OK
    assert str(missing) in probe.detail
    with _answering(401, pm_ai=True) as (port, seen):
        rejected = daemon_reachable(port, missing)
    assert "Authorization" not in seen[0]
    assert str(missing) in rejected.detail


# ── Review fixes: every other answer, the token rule, the one deadline ───────


@contextmanager
def _raw_peer(script) -> Iterator[int]:
    """A loopback listener that runs `script(connection)` for each connection."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.1)
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except OSError:
                continue
            with connection:
                try:
                    script(connection)
                except OSError:
                    pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join(timeout=5)
        listener.close()


def test_a_pm_ai_daemon_answering_an_error_status_is_a_warning(token_file):
    with _answering(503, pm_ai=True) as (port, _):
        probe = daemon_reachable(port, token_file)
    assert probe.health is Health.WARNING
    assert "HTTP 503" in probe.detail


def test_a_200_whose_body_is_not_json_is_status_could_not_be_read(token_file):
    with _answering(200, pm_ai=True, body=b"<html>no</html>") as (port, _):
        probe = daemon_reachable(port, token_file)
    assert probe.health is Health.WARNING
    assert "status could not be read" in probe.detail


def test_an_infinite_uptime_is_status_could_not_be_read(token_file):
    body = b'{"uptime_seconds": Infinity, "started_at": "x"}'
    with _answering(200, pm_ai=True, body=body) as (port, _):
        probe = daemon_reachable(port, token_file)
    assert probe.health is Health.WARNING
    assert "status could not be read" in probe.detail


def test_an_answer_that_is_not_http_is_something_else(token_file):
    def gibberish(connection: socket.socket) -> None:
        connection.recv(4096)
        connection.sendall(b"SSH-2.0-OpenSSH_9.0\r\n")

    with _raw_peer(gibberish) as port:
        probe = daemon_reachable(port, token_file)
    assert probe.health is Health.WARNING
    assert probe.detail.startswith("something that is not pm-ai answers")


def test_a_slow_drip_cannot_stretch_the_deadline(token_file):
    """A byte every 0.1 s keeps each read alive; the total is still bounded."""
    import time

    def drip(connection: socket.socket) -> None:
        connection.recv(4096)
        for byte in b"HTTP/1.1 200 OK\r\nx-pm-ai: 1\r\nContent-Length: 999\r\n\r\n" + b"x" * 999:
            connection.sendall(bytes([byte]))
            time.sleep(0.1)

    with _raw_peer(drip) as port:
        started = time.monotonic()
        probe = daemon_reachable(port, token_file, timeout=0.5)
        elapsed = time.monotonic() - started
    assert elapsed < 1.5
    assert probe.health is Health.WARNING
    assert probe.detail.startswith("not answering")


@pytest.mark.parametrize(
    "content",
    [b"x" * 20 + b"\r\nX-Evil: 1" + b"x" * 20, b"\xff" * 40, b"x" * 16],
    ids=["crlf", "non-ascii", "too-short"],
)
def test_a_token_the_daemon_would_refuse_is_named_and_never_sent(tmp_path, content):
    path = tmp_path / "api_token"
    path.write_bytes(content)
    path.chmod(0o600)
    with _answering(401, pm_ai=True) as (port, seen):
        probe = daemon_reachable(port, path)
    assert "Authorization" not in seen[0]
    assert "X-Evil" not in seen[0]
    assert "would refuse its token file" in probe.detail
    assert str(path) in probe.detail


def test_an_unreadable_token_file_is_named(tmp_path):
    path = tmp_path / "api_token"
    path.write_text(TOKEN)
    path.chmod(0o000)
    try:
        probe = daemon_reachable(_free_port(), path)
    finally:
        path.chmod(0o600)
    assert probe.health is Health.OK
    assert "could not be read" in probe.detail
    assert str(path) in probe.detail


def test_a_fallback_port_is_said(token_file):
    probe = daemon_reachable(_free_port(), token_file, port_note=" (default port asked)")
    assert probe.detail.endswith("(default port asked)")
