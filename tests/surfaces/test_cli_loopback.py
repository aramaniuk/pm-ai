"""Story 4q — the prompt's loopback client against a stand-in daemon over real HTTP.

The stand-in is the standard library's HTTP server on `127.0.0.1`, answering
each request from a script, so every answer the client must know — and every
one it must not — is a test. The token file is a real file in `tmp_path`.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pm_ai.core.config import ConfigRefused
from pm_ai.surfaces.api.app import IDENTITY_HEADER, PROMPT_PATH
from pm_ai.surfaces.cli import loopback
from pm_ai.surfaces.cli.loopback import (
    Acknowledged,
    Answer,
    Busy,
    LoopbackClient,
    NoDaemon,
    NotSent,
    StreamLost,
    TokenRejected,
    UnknownAnswer,
)

TOKEN = "t" * 43


@dataclass
class _Reply:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    identity: bool = True
    frames: list[bytes | float] | None = None
    """Written as an event stream, then the connection is closed; a number is a pause."""
    delay: float = 0.0
    """Seconds before anything is answered — the headers included."""
    drip: float = 0.0
    """Seconds between the body's bytes, written one at a time."""


@dataclass
class _Seen:
    method: str
    path: str
    authorization: str | None
    body: bytes


class _Daemon:
    """A stand-in daemon on a free loopback port, answering from `replies`."""

    def __init__(self, port: int = 0) -> None:
        self.replies: dict[tuple[str, str], _Reply] = {}
        self.seen: list[_Seen] = []
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                return

            def _answer(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                stand_in.seen.append(
                    _Seen(method, self.path, self.headers.get("Authorization"), body)
                )
                reply = stand_in.replies.get((method, self.path), _Reply(404, b'{"detail":"no"}'))
                time.sleep(reply.delay)
                self.send_response(reply.status)
                if reply.identity:
                    self.send_header(IDENTITY_HEADER, "1")
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                if reply.frames is not None:
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for frame in reply.frames:
                        if isinstance(frame, float):
                            time.sleep(frame)
                            continue
                        self.wfile.write(frame)
                        self.wfile.flush()
                    return
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                if not reply.drip:
                    self.wfile.write(reply.body)
                    return
                for byte in reply.body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(reply.drip)

            def do_POST(self) -> None:
                self._answer("POST")

            def do_GET(self) -> None:
                self._answer("GET")

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def daemon() -> Iterator[_Daemon]:
    stand_in = _Daemon()
    try:
        yield stand_in
    finally:
        stand_in.close()


def _token(path: Path, value: str = TOKEN, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n")
    os.chmod(path, mode)
    return path


def _client(token: Path, port: int) -> LoopbackClient:
    return LoopbackClient(token_path=lambda: token, port=lambda: port)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _json(status: int, body: object, **headers: str) -> _Reply:
    return _Reply(status, json.dumps(body).encode(), dict(headers))


# ── What a line is sent as ───────────────────────────────────────────────────


def test_a_line_is_posted_as_json_with_the_bearer_token(daemon, tmp_path):
    daemon.replies[("POST", PROMPT_PATH)] = _json(200, {"inline": True, "output": "x\n", "exit": 0})
    assert _client(_token(tmp_path / "api_token"), daemon.port).send("config show") == Answer("x\n", 0)
    (seen,) = daemon.seen
    assert (seen.method, seen.path) == ("POST", PROMPT_PATH)
    assert seen.authorization == f"Bearer {TOKEN}"
    assert json.loads(seen.body) == {"line": "config show"}


def test_an_acknowledgement_is_its_job_id(daemon, tmp_path):
    daemon.replies[("POST", PROMPT_PATH)] = _json(202, {"inline": False, "job_id": "job_ab"})
    assert _client(_token(tmp_path / "t"), daemon.port).send("dashboard") == Acknowledged("job_ab")


# ── The answers that are not a command's ─────────────────────────────────────


def test_no_token_file_is_no_daemon_and_nothing_is_sent(daemon, tmp_path):
    outcome = _client(tmp_path / "absent", daemon.port).send("dashboard")
    assert isinstance(outcome, NoDaemon)
    assert "`pm-ai daemon run`" in outcome.sentence
    assert daemon.seen == []


def test_a_refused_connection_is_no_daemon(tmp_path):
    port = _free_port()
    outcome = _client(_token(tmp_path / "t"), port).send("dashboard")
    assert isinstance(outcome, NoDaemon)
    assert f"127.0.0.1:{port}" in outcome.sentence and "`pm-ai daemon run`" in outcome.sentence


def test_a_401_is_the_token_rejected(daemon, tmp_path):
    daemon.replies[("POST", PROMPT_PATH)] = _json(401, {"detail": "a valid token is required"})
    token = _token(tmp_path / "t")
    outcome = _client(token, daemon.port).send("dashboard")
    assert outcome == TokenRejected(token)
    assert "disagree" in outcome.sentence and "restart the daemon" in outcome.sentence


def test_a_503_with_retry_after_is_busy_with_its_wait_and_is_not_retried(daemon, tmp_path):
    daemon.replies[("POST", PROMPT_PATH)] = _json(503, {"detail": "busy"}, **{"Retry-After": "1"})
    outcome = _client(_token(tmp_path / "t"), daemon.port).send("dashboard")
    assert outcome == Busy(1)
    assert outcome.sentence.endswith("try again in 1 second.")
    assert len(daemon.seen) == 1


@pytest.mark.parametrize(
    ("reply", "named"),
    [
        (_json(404, {"detail": "Not Found"}), "HTTP 404"),
        (_json(500, {"detail": "boom"}), "HTTP 500"),
        (_json(502, {"detail": "boom"}), "HTTP 502"),
        (_Reply(200, b"<html>not json</html>"), "not JSON"),
        (_json(200, {"inline": True}), "not the answer to a line"),
        (_Reply(200, b'{"inline": true, "output": "", "exit": 0}', identity=False), "not a pm-ai daemon"),
    ],
)
def test_an_answer_the_client_does_not_know_names_its_status(daemon, tmp_path, reply, named):
    daemon.replies[("POST", PROMPT_PATH)] = reply
    outcome = _client(_token(tmp_path / "t"), daemon.port).send("dashboard")
    assert isinstance(outcome, UnknownAnswer)
    assert named in outcome.sentence
    assert f"HTTP {reply.status}" in outcome.sentence


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o606])
def test_a_token_file_with_the_wrong_mode_is_named_and_nothing_is_sent(daemon, tmp_path, mode):
    token = _token(tmp_path / "api_token", mode=mode)
    outcome = _client(token, daemon.port).send("dashboard")
    assert isinstance(outcome, NotSent)
    assert str(token) in outcome.sentence and "not sent" in outcome.sentence
    assert daemon.seen == []


def test_a_token_file_that_is_a_link_is_named_and_nothing_is_sent(daemon, tmp_path):
    real = _token(tmp_path / "real")
    link = tmp_path / "api_token"
    link.symlink_to(real)
    outcome = _client(link, daemon.port).send("dashboard")
    assert isinstance(outcome, NotSent) and str(link) in outcome.sentence
    assert daemon.seen == []


def test_a_config_toml_the_loader_refuses_sends_nothing(daemon, tmp_path):
    def refused() -> int:
        raise ConfigRefused("`daemon_port` must be a whole number")

    client = LoopbackClient(token_path=lambda: _token(tmp_path / "t"), port=refused)
    outcome = client.send("dashboard")
    assert isinstance(outcome, NotSent) and "daemon_port" in outcome.sentence
    assert daemon.seen == []


# ── Read on every request, never at open ─────────────────────────────────────


def test_a_daemon_started_after_the_client_was_built_is_reached(tmp_path):
    token = tmp_path / "private" / "api_token"
    port = _free_port()
    client = _client(token, port)  # built with no token file and no daemon
    assert isinstance(client.send("config show"), NoDaemon)
    _token(token)
    assert isinstance(client.send("config show"), NoDaemon), "a token, but nothing listens"
    stand_in = _Daemon(port)  # the daemon starts; the same client reaches it
    try:
        stand_in.replies[("POST", PROMPT_PATH)] = _json(200, {"inline": True, "output": "ok\n", "exit": 0})
        assert client.send("config show") == Answer("ok\n", 0)
    finally:
        stand_in.close()


def test_the_token_and_port_are_asked_on_every_request(daemon, tmp_path):
    asked: list[str] = []
    token = _token(tmp_path / "t")

    def path() -> Path:
        asked.append("token")
        return token

    def port() -> int:
        asked.append("port")
        return daemon.port

    client = LoopbackClient(token_path=path, port=port)
    assert asked == []
    daemon.replies[("POST", PROMPT_PATH)] = _json(200, {"inline": True, "output": "", "exit": 0})
    client.send("config show")
    _token(token, "u" * 43)
    client.send("config show")
    assert asked == ["port", "token", "port", "token"]
    assert [seen.authorization for seen in daemon.seen] == [f"Bearer {TOKEN}", f"Bearer {'u' * 43}"]


# ── The job's stream ─────────────────────────────────────────────────────────


def _frame(state: str, result: object = None) -> bytes:
    row = {"id": "job_ab", "kind": "dashboard", "scope": "personal", "state": state, "result": result}
    return f"data: {json.dumps(row)}\n\n".encode()


def test_following_a_job_reads_its_stream_until_it_is_done(daemon, tmp_path):
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(
        frames=[_frame("queued"), _frame("running"), _frame("done", {"output": "written\n", "exit": 0})]
    )
    followed = _client(_token(tmp_path / "t"), daemon.port).follow("job_ab")
    assert followed == Answer("written\n", 0)
    assert daemon.seen[0].authorization == f"Bearer {TOKEN}"


def test_a_failed_job_is_its_sentence_and_exit_1(daemon, tmp_path):
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(
        frames=[_frame("failed", {"error": "the claim on meetings/ is held", "type": "ArtifactBusy"})]
    )
    followed = _client(_token(tmp_path / "t"), daemon.port).follow("job_ab")
    assert followed == Answer("pm-ai: the claim on meetings/ is held\n", 1)


def test_a_stream_that_ends_before_the_result_is_lost_naming_the_job(daemon, tmp_path):
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(frames=[_frame("running")])
    followed = _client(_token(tmp_path / "t"), daemon.port).follow("job_ab")
    assert isinstance(followed, StreamLost)
    assert "job_ab" in followed.sentence


def test_a_stream_the_daemon_does_not_know_is_lost(daemon, tmp_path):
    followed = _client(_token(tmp_path / "t"), daemon.port).follow("job_ab")
    assert isinstance(followed, StreamLost) and "HTTP 404" in followed.sentence


def test_the_client_names_only_the_loopback_address():
    assert loopback.LOOPBACK == "127.0.0.1"


# ── Story 4q review: Retry-After ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("header", "wait", "said"),
    [
        ({"Retry-After": "1"}, 1, "try again in 1 second."),
        ({"Retry-After": "7"}, 7, "try again in 7 seconds."),
        ({"Retry-After": "0"}, 0, "try again now."),
        ({}, 0, "try again now."),
        ({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, None, "try again shortly."),
        ({"Retry-After": "\uff11"}, None, "try again shortly."),
        ({"Retry-After": "-1"}, None, "try again shortly."),
    ],
)
def test_retry_after_reads_only_ascii_digits_and_never_raises(daemon, tmp_path, header, wait, said):
    reply = _json(503, {"detail": "busy"})
    reply.headers = {name: value.encode("utf-8").decode("latin-1") for name, value in header.items()}
    daemon.replies[("POST", PROMPT_PATH)] = reply
    outcome = _client(_token(tmp_path / "t"), daemon.port).send("dashboard")
    assert isinstance(outcome, Busy) and outcome.seconds == wait
    assert outcome.sentence.endswith(said)


# ── Story 4q review: the timeouts ────────────────────────────────────────────


def test_an_inline_answer_slower_than_the_connect_timeout_is_still_an_answer(daemon, tmp_path):
    daemon.replies[("POST", PROMPT_PATH)] = _json(200, {"inline": True, "output": "slow\n", "exit": 0})
    daemon.replies[("POST", PROMPT_PATH)].delay = loopback.CONNECT_SECONDS + 0.5
    assert _client(_token(tmp_path / "t"), daemon.port).send("config show") == Answer("slow\n", 0)


def test_the_answer_limit_is_one_deadline_however_the_answer_is_paced(daemon, tmp_path):
    body = json.dumps({"inline": True, "output": "x" * 40, "exit": 0}).encode()
    daemon.replies[("POST", PROMPT_PATH)] = _Reply(200, body, drip=0.05)  # ~3 s in all
    client = LoopbackClient(
        token_path=lambda: _token(tmp_path / "t"), port=lambda: daemon.port, answer_seconds=1.0
    )
    outcome = client.send("config show")
    assert isinstance(outcome, UnknownAnswer) and outcome.status is None
    assert "within 1 seconds" in outcome.sentence


def test_a_connect_timeout_says_nothing_answered_and_the_line_was_not_sent(tmp_path, monkeypatch):
    def slow(*args: object, **kwargs: object) -> object:
        raise TimeoutError("timed out")

    monkeypatch.setattr(loopback.socket, "create_connection", slow)
    outcome = _client(_token(tmp_path / "t"), 8747).send("config show")
    assert isinstance(outcome, NotSent)
    assert "nothing answered on 127.0.0.1:8747 within 2 seconds" in outcome.sentence
    assert "not sent" in outcome.sentence
    assert "30 seconds" not in outcome.sentence and "may still finish" not in outcome.sentence


def test_a_stream_pausing_longer_than_the_connect_timeout_still_returns_the_result(daemon, tmp_path):
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(
        frames=[_frame("running"), loopback.CONNECT_SECONDS + 0.5, _frame("done", {"output": "ok\n", "exit": 0})]
    )
    assert _client(_token(tmp_path / "t"), daemon.port).follow("job_ab") == Answer("ok\n", 0)


def test_a_stream_slow_to_send_its_headers_still_returns_the_result(daemon, tmp_path):
    reply = _Reply(frames=[_frame("done", {"output": "ok\n", "exit": 0})], delay=loopback.CONNECT_SECONDS + 0.5)
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = reply
    assert _client(_token(tmp_path / "t"), daemon.port).follow("job_ab") == Answer("ok\n", 0)


# ── Story 4q review: the stream is held to the same rules ────────────────────


def test_a_stream_from_something_that_is_not_pm_ai_is_lost(daemon, tmp_path):
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(frames=[_frame("done", {"output": "", "exit": 0})], identity=False)
    followed = _client(_token(tmp_path / "t"), daemon.port).follow("job_ab")
    assert isinstance(followed, StreamLost) and "not a pm-ai daemon" in followed.sentence


def test_a_stream_refused_401_says_the_token_and_the_daemon_disagree(daemon, tmp_path):
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _json(401, {"detail": "a valid token is required"})
    token = _token(tmp_path / "t")
    followed = _client(token, daemon.port).follow("job_ab")
    assert isinstance(followed, StreamLost)
    assert TokenRejected(token).sentence.rstrip(".") in followed.sentence


def test_an_event_without_its_closing_blank_line_is_still_read(daemon, tmp_path):
    frame = _frame("done", {"output": "ok\n", "exit": 0}).rstrip(b"\n") + b"\n"
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(frames=[frame])
    assert _client(_token(tmp_path / "t"), daemon.port).follow("job_ab") == Answer("ok\n", 0)


def test_a_stream_line_over_the_limit_is_named(daemon, tmp_path, monkeypatch):
    monkeypatch.setattr(loopback, "MAX_EVENT_LINE_BYTES", 64)
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(frames=[_frame("done", {"output": "x" * 200, "exit": 0})])
    followed = _client(_token(tmp_path / "t"), daemon.port).follow("job_ab")
    assert isinstance(followed, StreamLost) and "longer than" in followed.sentence


@pytest.mark.parametrize(
    ("frame", "named"),
    [
        (b"data: {not json\n\n", "not JSON"),
        (b"data: [1, 2]\n\n", "not a job's row"),
        (b'data: {"state": "done"}\n\n', "carried no result"),
        (b'data: {"state": "done", "result": {"output": 3}}\n\n', "not a command's output"),
    ],
)
def test_a_malformed_event_is_named(daemon, tmp_path, frame, named):
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(frames=[frame])
    followed = _client(_token(tmp_path / "t"), daemon.port).follow("job_ab")
    assert isinstance(followed, StreamLost) and named in followed.sentence


def test_a_stream_that_cannot_be_read_for_its_token_does_not_say_the_line_was_not_run(tmp_path):
    followed = _client(_token(tmp_path / "t", mode=0o644), 8747).follow("job_ab")
    assert isinstance(followed, StreamLost)
    assert "job_ab" in followed.sentence and "accepted it" in followed.sentence
    assert "not run" not in followed.sentence and "not sent" not in followed.sentence


# ── Story 4q review: answers that are too large, or not HTTP ─────────────────


def test_an_answer_over_the_limit_is_named_as_too_large(daemon, tmp_path, monkeypatch):
    monkeypatch.setattr(loopback, "MAX_ANSWER_BYTES", 300)
    daemon.replies[("POST", PROMPT_PATH)] = _json(200, {"inline": True, "output": "x" * 1000, "exit": 0})
    outcome = _client(_token(tmp_path / "t"), daemon.port).send("config show")
    assert isinstance(outcome, UnknownAnswer) and "larger than" in outcome.sentence
    assert "JSON" not in outcome.sentence


class _FakeSocket:
    def __init__(self, answer: bytes | BaseException) -> None:
        self._answer = answer
        self._given = False

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def settimeout(self, seconds: float | None) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        return None

    def recv(self, size: int) -> bytes:
        if isinstance(self._answer, BaseException):
            raise self._answer
        if self._given:
            return b""
        self._given = True
        return self._answer


@pytest.mark.parametrize(
    ("answer", "named"),
    [
        (ConnectionResetError(54, "Connection reset by peer"), "could not be asked (Connection reset by peer)"),
        (b"hello there\r\n\r\n", "did not answer as HTTP"),
    ],
)
def test_a_connection_that_fails_after_sending_is_unknown_not_a_crash(tmp_path, monkeypatch, answer, named):
    monkeypatch.setattr(loopback.socket, "create_connection", lambda *a, **k: _FakeSocket(answer))
    outcome = _client(_token(tmp_path / "t"), 8747).send("config show")
    assert isinstance(outcome, UnknownAnswer) and named in outcome.sentence


def test_an_unexpected_error_is_an_outcome_never_raised(daemon, tmp_path, monkeypatch):
    def broken(*args: object) -> object:
        raise RuntimeError("boom")

    daemon.replies[("POST", PROMPT_PATH)] = _json(200, {"inline": True, "output": "", "exit": 0})
    monkeypatch.setattr(loopback, "_judge", broken)
    outcome = _client(_token(tmp_path / "t"), daemon.port).send("config show")
    assert isinstance(outcome, UnknownAnswer) and "RuntimeError" in outcome.sentence
    monkeypatch.setattr(loopback, "_events", broken)
    daemon.replies[("GET", "/v1/jobs/job_ab/events")] = _Reply(frames=[_frame("running")])
    followed = _client(_token(tmp_path / "t"), daemon.port).follow("job_ab")
    assert isinstance(followed, StreamLost) and "RuntimeError" in followed.sentence


def test_a_token_path_that_cannot_be_worked_out_sends_nothing(daemon):
    def broken() -> Path:
        raise RuntimeError("no home")

    outcome = LoopbackClient(token_path=broken, port=lambda: daemon.port).send("config show")
    assert isinstance(outcome, NotSent) and "not sent" in outcome.sentence
    assert daemon.seen == []


def test_ctrl_c_says_whether_the_line_was_sent(tmp_path, monkeypatch):
    class _Interrupted(_FakeSocket):
        def sendall(self, data: bytes) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(loopback.socket, "create_connection", lambda *a, **k: _Interrupted(b""))
    with pytest.raises(loopback.WaitAbandoned) as before:
        _client(_token(tmp_path / "t"), 8747).send("config show")
    assert before.value.sent is False
    monkeypatch.setattr(loopback.socket, "create_connection", lambda *a, **k: _FakeSocket(KeyboardInterrupt()))
    with pytest.raises(loopback.WaitAbandoned) as after:
        _client(_token(tmp_path / "t"), 8747).send("config show")
    assert after.value.sent is True


# ── Story 4q review: the token file's owner and kind ─────────────────────────


def test_a_token_file_another_account_owns_is_named_and_nothing_is_sent(daemon, tmp_path, monkeypatch):
    token = _token(tmp_path / "api_token")
    monkeypatch.setattr(loopback.os, "getuid", lambda: os.stat(token).st_uid + 1)
    outcome = _client(token, daemon.port).send("config show")
    assert isinstance(outcome, NotSent) and "another account" in outcome.sentence
    assert daemon.seen == []


def test_a_token_path_that_is_a_fifo_or_a_directory_is_named(daemon, tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    directory = tmp_path / "directory"
    directory.mkdir(mode=0o700)
    for path in (fifo, directory):
        outcome = _client(path, daemon.port).send("config show")
        assert isinstance(outcome, NotSent), path
        assert "not a regular file" in outcome.sentence and str(path) in outcome.sentence
    assert daemon.seen == []


# ── Story 4q review: only `daemon_port` is read ──────────────────────────────


def test_only_daemon_port_is_read_from_config_toml():
    from pm_ai.core.config import DEFAULT_DAEMON_PORT, read_daemon_port

    assert read_daemon_port(None) == DEFAULT_DAEMON_PORT
    assert read_daemon_port(b"display_timezone = 'Mars/Olympus'\nbogus = 1\n") == DEFAULT_DAEMON_PORT
    assert read_daemon_port(b"bogus = 1\ndaemon_port = 9001\n") == 9001
    for bad in (b"daemon_port = 'x'\n", b"daemon_port = 80\n", b"daemon_port = \n"):
        with pytest.raises(ConfigRefused):
            read_daemon_port(bad)
