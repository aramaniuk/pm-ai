"""`pm-ai daemon run` end to end — story 4e's lifecycle rows, in a throwaway home.

Two kinds of row. The ones about a running daemon start the real console script
as a separate process, on a free port named in the throwaway home's
`config.toml`, and talk to it over loopback exactly as another pm-ai process
would: a signal, a second start and `pm-ai doctor` are all things only a real
process can be on the other end of. The keychain is `keyring`'s null backend in
those processes, so nothing reaches the developer's login keychain.

The refusals — a taken port, a bad token file, a missing extra — are refused
before anything is served, so they run in-process through `entry.main` with the
keychain stood in, like every other CLI row in the suite.
"""

from __future__ import annotations

import errno
import http.client
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from pm_ai.app import daemon as daemon_module
from pm_ai.app import entry
from pm_ai.core.config import ConfigRefused, load_config
from pm_ai.ports import KeyNotFound
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL

STARTUP_SECONDS = 30.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _console_script() -> list[str]:
    script = Path(sys.executable).with_name("pm-ai")
    return [str(script)] if script.exists() else [sys.executable, "-m", "pm_ai.app.entry"]


@dataclass
class Home:
    """A throwaway home with `daemon_port` set to a free port."""

    root: Path
    port: int

    @property
    def application(self) -> Path:
        return self.root / ".pm-ai"

    @property
    def token_file(self) -> Path:
        return self.application / "private" / "api_token"

    @property
    def env(self) -> dict[str, str]:
        return {
            **os.environ,
            "HOME": str(self.root),
            "PM_AI_DISABLE_ENCRYPTION": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        }

    def run(self, *argv: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*_console_script(), *argv],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=self.root,
        )

    def start(self) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [*_console_script(), "daemon", "run"],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.root,
        )

    def get(self, path: str, token: str | None = None) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            headers = {} if token is None else {"Authorization": f"Bearer {token}"}
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def log_lines(self) -> list[dict[str, object]]:
        logs = self.application / "logs"
        return [
            json.loads(line)
            for path in sorted(logs.iterdir())
            for line in path.read_text().splitlines()
        ]


@pytest.fixture
def home(tmp_path: Path) -> Home:
    root = tmp_path / "home"
    (root / ".pm-ai").mkdir(parents=True)
    port = _free_port()
    (root / ".pm-ai" / "config.toml").write_text(f"daemon_port = {port}\n")
    return Home(root, port)


def _wait_listening(process: subprocess.Popen[str]) -> str:
    """The announcement line, or fail with whatever the process said instead."""
    assert process.stdout is not None
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], 0.2)
        if ready:
            line = process.stdout.readline()
            if "listening on" in line:
                return line
            if not line and process.poll() is not None:
                break
        elif process.poll() is not None:
            break
    process.kill()
    out, err = process.communicate()
    pytest.fail(f"the daemon never announced itself:\nstdout: {out}\nstderr: {err}")


def _stop(process: subprocess.Popen[str], sig: int = signal.SIGTERM) -> tuple[int, str, str]:
    process.send_signal(sig)
    try:
        out, err = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
        pytest.fail(f"the daemon did not stop on {signal.Signals(sig).name}: {err}")
    return process.returncode, out, err


@pytest.fixture
def running(home: Home) -> Iterator[tuple[Home, subprocess.Popen[str], str]]:
    process = home.start()
    try:
        announced = _wait_listening(process)
        yield home, process, announced
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


# ── A running daemon ─────────────────────────────────────────────────────────


def test_the_first_start_mints_an_owner_only_token_and_listens_on_loopback(running):
    home, process, announced = running
    assert f"listening on 127.0.0.1:{home.port}" in announced
    token = home.token_file.read_text()
    assert len(token) >= 32
    assert (home.token_file.stat().st_mode & 0o777) == 0o600
    status, _, _ = home.get("/v1/status", token)
    assert status == 200


def test_the_status_answers_with_the_token_and_refuses_without(running):
    """The slice's own acceptance: `401` without, the doctor's probes with."""
    home, _, _ = running
    token = home.token_file.read_text()

    status, headers, body = home.get("/v1/status?debug=1")
    assert status == 401
    assert json.loads(body) == {"detail": "a valid token is required"}

    status, _, _ = home.get("/v1/status", "w" * 43)
    assert status == 401

    status, _, body = home.get("/v1/status", token)
    assert status == 200
    report = json.loads(body)
    assert set(report) == {"probes", "healthy", "uptime_seconds", "started_at"}
    names = [probe["name"] for probe in report["probes"]]
    assert "daemon" not in names
    assert "project registry" in names
    for probe in report["probes"]:
        assert set(probe) == {"name", "state", "detail"}
    assert isinstance(report["healthy"], bool)
    assert isinstance(report["uptime_seconds"], int)
    assert report["started_at"].endswith("+00:00")


def test_the_request_log_has_the_path_and_never_the_query_or_the_token(running):
    home, _, _ = running
    token = home.token_file.read_text()
    home.get("/v1/status?debug=1")
    home.get("/v1/anything?x=1", token)
    home.get("/v1/status", token)

    lines = home.log_lines()
    messages = [line["message"] for line in lines]
    assert messages == ["GET /v1/status 401", "GET /v1/anything 404", "GET /v1/status 200"]
    text = "\n".join(path.read_text() for path in (home.application / "logs").iterdir())
    assert "?" not in text
    assert "debug" not in text
    assert "x=1" not in text
    assert token not in text
    assert "Bearer" not in text
    # AD-24: the diagnostic log, never the event log.
    assert not (home.application / "memory").exists()


def test_doctor_in_another_process_sees_it_running_and_a_second_start_is_refused(running):
    home, _, _ = running
    doctor = home.run("doctor")
    daemon_lines = [line for line in doctor.stdout.splitlines() if "] daemon:" in line]
    assert len(daemon_lines) == 1, doctor.stdout
    line = daemon_lines[0]
    assert line.lstrip().startswith("[     ok] daemon: running")
    assert f"127.0.0.1:{home.port}" in line
    assert "started at" in line

    token_before = home.token_file.read_bytes()
    second = home.run("daemon", "run")
    assert second.returncode == EXIT_REFUSAL
    assert f"port {home.port}" in second.stderr
    assert "daemon_port" in second.stderr
    assert home.token_file.read_bytes() == token_before


def test_encryption_off_warns_once_at_start_and_writes_no_event_log_line(running):
    home, process, _ = running
    home.get("/v1/status", home.token_file.read_text())
    code, _, err = _stop(process)
    assert code == 0
    assert err.count("WARNING: encryption is disabled") == 1
    assert not (home.application / "memory").exists()


@pytest.mark.parametrize(
    "sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP], ids=["term", "int", "hup"]
)
def test_a_stop_signal_stops_listening_and_exits_0(home, sig):
    process = home.start()
    try:
        _wait_listening(process)
        code, _, err = _stop(process, sig)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert code == 0, err
    assert "Traceback" not in err
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", home.port), timeout=1).close()


def test_a_later_start_reads_the_same_token_and_mints_none(home):
    first = home.start()
    try:
        _wait_listening(first)
    finally:
        _stop(first)
    token = home.token_file.read_bytes()
    minted_at = home.token_file.stat().st_mtime_ns

    second = home.start()
    try:
        _wait_listening(second)
        status, _, _ = home.get("/v1/status", token.decode())
        assert status == 200
    finally:
        _stop(second)
    assert home.token_file.read_bytes() == token
    assert home.token_file.stat().st_mtime_ns == minted_at


def test_two_starts_at_once_leave_one_daemon_and_one_token(home):
    """The port decides; the loser refuses naming it and the token is the winner's."""
    first, second = home.start(), home.start()
    try:
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline and first.poll() is None and second.poll() is None:
            time.sleep(0.1)
        loser, winner = (first, second) if first.poll() is not None else (second, first)
        assert loser.poll() is not None, "neither start was refused"
        _, loser_err = loser.communicate()
        assert loser.returncode == EXIT_REFUSAL
        assert f"port {home.port}" in loser_err
        _wait_listening(winner)
        status, _, _ = home.get("/v1/status", home.token_file.read_text())
        assert status == 200
    finally:
        for process in (first, second):
            if process.poll() is None:
                _stop(process)


# ── Refusals before anything is served (in-process) ──────────────────────────


class _Keychain:
    """A keychain holding nothing — every row here is refused before it is asked."""

    def store(self, name: str, secret: bytes) -> None:  # pragma: no cover
        raise AssertionError("nothing here enrols a key")

    def store_if_absent(self, name: str, secret: bytes) -> None:  # pragma: no cover
        raise AssertionError("nothing here enrols a key")

    def fetch(self, name: str) -> bytes:
        raise KeyNotFound(name)

    def delete(self, name: str) -> None:  # pragma: no cover
        raise AssertionError("nothing here deletes a key")


@pytest.fixture
def machine(home: Home, monkeypatch) -> Home:
    monkeypatch.setenv("HOME", str(home.root))
    monkeypatch.delenv("PM_AI_DISABLE_ENCRYPTION", raising=False)
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", _Keychain)
    monkeypatch.chdir(home.root)
    return home


@pytest.mark.parametrize(
    "content",
    [b"x" * 16, b"x" * 31, b"", b"  \n\t "],
    ids=["16-characters", "31-characters", "empty", "whitespace"],
)
def test_a_bad_token_file_refuses_the_start_naming_it_and_is_unchanged(
    machine, capsys, content
):
    machine.token_file.parent.mkdir(parents=True)
    machine.token_file.write_bytes(content)
    machine.token_file.chmod(0o600)
    assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert str(machine.token_file) in err
    assert machine.token_file.read_bytes() == content
    assert "Traceback" not in err


def test_a_group_readable_token_file_refuses_the_start(machine, capsys):
    machine.token_file.parent.mkdir(parents=True)
    machine.token_file.write_text("g" * 43)
    machine.token_file.chmod(0o640)
    assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert str(machine.token_file) in err
    assert "g" * 43 not in err
    assert (machine.token_file.stat().st_mode & 0o777) == 0o640


def test_an_unreadable_token_file_refuses_the_start(machine, capsys):
    machine.token_file.parent.mkdir(parents=True)
    machine.token_file.write_text("u" * 43)
    machine.token_file.chmod(0o000)
    try:
        assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    finally:
        machine.token_file.chmod(0o600)
    assert str(machine.token_file) in capsys.readouterr().err
    assert machine.token_file.read_text() == "u" * 43


def test_a_taken_port_refuses_the_start_before_the_token_is_touched(machine, capsys):
    holder = socket.socket()
    holder.bind(("127.0.0.1", machine.port))
    holder.listen(1)
    try:
        assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    finally:
        holder.close()
    err = capsys.readouterr().err
    assert f"port {machine.port}" in err
    assert "daemon_port" in err
    assert not machine.token_file.exists()


def test_the_bind_itself_refuses_a_port_another_socket_is_bound_to(machine, capsys, monkeypatch):
    """The second of the two checks, with the first stood down: a listening socket
    on the port, and `_answers` told it saw nothing, so only the bind can refuse."""
    monkeypatch.setattr(daemon_module, "_answers", lambda port: False)
    holder = socket.socket()
    holder.bind(("127.0.0.1", machine.port))
    holder.listen(1)
    try:
        assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    finally:
        holder.close()
    err = capsys.readouterr().err
    assert f"port {machine.port}" in err
    assert not machine.token_file.exists()


def test_a_missing_runtime_extra_refuses_the_start_naming_it(machine, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "runtime" in err
    assert "uv sync --extra runtime" in err
    assert not machine.token_file.exists()


@pytest.mark.parametrize(
    "line", ["daemon_port = 80", "daemon_port = 8747.0", 'daemon_port = "8747"', "daemon_port = true"]
)
def test_an_unusable_port_setting_refuses_the_start_with_the_loaders_sentence(
    machine, capsys, line
):
    raw = f"{line}\n".encode()
    (machine.application / "config.toml").write_bytes(raw)
    with pytest.raises(ConfigRefused) as refused:
        load_config(raw)
    assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    assert str(refused.value) in capsys.readouterr().err
    assert not machine.token_file.exists()


def test_the_daemon_command_says_it_runs_in_the_foreground_on_the_port(capsys):
    assert entry.main(["daemon"]) != EXIT_OK
    usage = capsys.readouterr()
    text = usage.out + usage.err
    assert "foreground" in text
    assert "8747" in text
    assert "daemon_port" in text
    assert "later slice" in text


def test_a_bound_socket_that_is_not_listening_is_refused_by_the_bind(machine, capsys):
    """A socket bound to the port and not listening still makes it taken.

    Measured on macOS: a connect to it does not finish (`EAGAIN`), which the
    first check counts as taken; were it refused instead, the bind would
    refuse. Either way the start is refused naming the port, before the token.
    """
    holder = socket.socket()
    holder.bind(("127.0.0.1", machine.port))
    try:
        assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    finally:
        holder.close()
    err = capsys.readouterr().err
    assert f"port {machine.port}" in err
    assert not machine.token_file.exists()


@pytest.mark.parametrize(
    "outcome",
    [
        ("returns", errno.EINPROGRESS, True),
        ("returns", errno.EAGAIN, True),
        ("returns", errno.ETIMEDOUT, True),
        ("returns", 0, True),
        ("returns", errno.ECONNREFUSED, False),
        ("raises", TimeoutError(), True),
    ],
    ids=["in-progress", "eagain", "timed-out", "accepted", "refused", "timeout"],
)
def test_only_a_refused_connect_reads_the_port_as_free(monkeypatch, outcome):
    how, value, taken = outcome

    class Probe:
        def __init__(self, *args: object) -> None:
            pass

        def settimeout(self, seconds: float) -> None:
            pass

        def connect_ex(self, address: object) -> int:
            if how == "raises":
                raise value
            return value

        def close(self) -> None:
            pass

    monkeypatch.setattr(daemon_module.socket, "socket", Probe)
    assert daemon_module._answers(8747) is taken


def test_a_lifespan_that_fails_refuses_the_start_rather_than_exiting_0(machine, capsys, monkeypatch):
    import contextlib

    @contextlib.asynccontextmanager
    async def broken(app):
        raise RuntimeError("a background job could not attach")
        yield

    monkeypatch.setattr(daemon_module, "_lifespan", broken)
    assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    assert "failed to start serving" in capsys.readouterr().err
    # It got as far as the token, and stopped listening on the way out.
    assert machine.token_file.exists()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", machine.port), timeout=1).close()


def test_a_request_log_that_cannot_be_opened_refuses_the_start(machine, capsys):
    (machine.application / "logs").write_text("a file where the directory goes")
    assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "request log" in err
    assert "logs" in err


def test_a_busy_store_while_composing_the_unenrolled_daemon_refuses_by_name(
    machine, capsys, monkeypatch
):
    from pm_ai.storage.service import OperationalStoreBusy

    def busy(*args: object, **kwargs: object) -> None:
        raise OperationalStoreBusy("the operational store at /x/operational.db stayed locked")

    monkeypatch.setattr(entry, "build", busy)
    assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    assert "the operational store at /x/operational.db stayed locked" in capsys.readouterr().err


def test_detaching_the_request_log_puts_the_logger_back():
    import logging

    from pm_ai.surfaces.api.app import REQUEST_LOGGER

    class Storage:
        def diagnostic_log(self, name: str) -> logging.Handler:
            return logging.NullHandler()

    class Daemon:
        storage = Storage()

    logger = logging.getLogger(REQUEST_LOGGER)
    logger.setLevel(logging.WARNING)
    logger.propagate = True
    attached = daemon_module._RequestLog.attach(Daemon())  # type: ignore[arg-type]
    assert logger.propagate is False and logger.level == logging.INFO
    attached.detach()
    assert logger.propagate is True and logger.level == logging.WARNING
    assert not logger.handlers
    logger.setLevel(logging.NOTSET)


# ── With a project enrolled ──────────────────────────────────────────────────


def _enrol(home: Home, project: Path) -> None:
    from pm_ai.core.project_registry import ProjectEntry, render_registry

    project.mkdir(parents=True, exist_ok=True)
    (home.application / "projects.toml").write_bytes(
        render_registry({"alpha": ProjectEntry(path=project)})
    )


def test_with_a_project_enrolled_the_daemon_serves_and_stops_cleanly(home, tmp_path):
    _enrol(home, tmp_path / "alpha")
    process = home.start()
    try:
        _wait_listening(process)
        status, _, body = home.get("/v1/status", home.token_file.read_text())
        assert status == 200
        registry = {p["name"]: p for p in json.loads(body)["probes"]}["project registry"]
        assert registry["state"] == "ok"
        code, _, err = _stop(process)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert code == 0, err


def test_with_a_project_enrolled_a_bad_port_setting_carries_the_loaders_sentence(
    machine, capsys, tmp_path
):
    _enrol(machine, tmp_path / "alpha")
    raw = b"daemon_port = 80\n"
    (machine.application / "config.toml").write_bytes(raw)
    with pytest.raises(ConfigRefused) as refused:
        load_config(raw)
    assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    assert str(refused.value) in capsys.readouterr().err
    assert not machine.token_file.exists()


def test_a_socket_the_server_cannot_serve_on_refuses_naming_the_port(machine, capsys, monkeypatch):
    """`create_server(sock=...)` failing comes out of uvicorn as a bare `OSError`."""

    def fails(coroutine):
        coroutine.close()
        raise OSError(errno.EBADF, "Bad file descriptor")

    monkeypatch.setattr(daemon_module.asyncio, "run", fails)
    assert entry.main(["daemon", "run"]) == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert f"127.0.0.1:{machine.port}" in err
    assert "daemon_port" in err
    assert "Traceback" not in err


def test_uvicorn_still_has_what_the_signal_override_relies_on():
    """`_server_class` overrides two non-public uvicorn methods; a bump that
    removes or reshapes either must fail here, naming it, not at a Ctrl-C."""
    import inspect

    import uvicorn

    server = uvicorn.Server
    assert callable(getattr(server, "capture_signals", None)), (
        "uvicorn.Server.capture_signals is gone; pm_ai.app.daemon._server_class overrides it"
    )
    assert list(inspect.signature(server.capture_signals).parameters) == ["self"], (
        "uvicorn.Server.capture_signals changed signature; _server_class overrides it"
    )
    assert "signal.raise_signal" in inspect.getsource(server.capture_signals), (
        "uvicorn.Server.capture_signals no longer re-raises; re-check _server_class"
    )
    startup = getattr(server, "startup", None)
    assert startup is not None and inspect.iscoroutinefunction(startup), (
        "uvicorn.Server.startup is gone or no longer async; _server_class overrides it"
    )
    parameters = inspect.signature(startup).parameters
    assert list(parameters) == ["self", "sockets"], (
        "uvicorn.Server.startup(sockets=...) changed signature; _server_class overrides it"
    )
    assert "started = True" in inspect.getsource(startup).replace("self.", ""), (
        "uvicorn.Server.startup no longer sets `started`; serve() reads it"
    )
    assert "sockets" in inspect.signature(server.serve).parameters, (
        "uvicorn.Server.serve(sockets=...) is gone; serve() passes its own listener"
    )
