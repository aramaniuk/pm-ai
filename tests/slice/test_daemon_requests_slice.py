"""Story 4r end to end: the real console script as a daemon, in a throwaway home.

Started as a separate process on a free port named in the throwaway home's
`config.toml`, with `keyring`'s null (or failing) backend so nothing reaches
the developer's login keychain, and spoken to over loopback exactly as `4q`'s
client will.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pm_ai.app.daemon import NO_KEY_ENROLLED
from test_daemon_slice import announced_lines


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _console_script() -> list[str]:
    script = Path(sys.executable).with_name("pm-ai")
    return [str(script)] if script.exists() else [sys.executable, "-m", "pm_ai.app.entry"]


@dataclass
class Home:
    root: Path
    port: int
    encryption: bool = False
    backend: str = "keyring.backends.null.Keyring"

    @property
    def application(self) -> Path:
        return self.root / ".pm-ai"

    @property
    def store(self) -> Path:
        return self.application / "private" / "operational.db"

    @property
    def token(self) -> str:
        return (self.application / "private" / "api_token").read_text()

    @property
    def env(self) -> dict[str, str]:
        env = {**os.environ, "HOME": str(self.root), "PYTHON_KEYRING_BACKEND": self.backend}
        if self.encryption:
            env.pop("PM_AI_DISABLE_ENCRYPTION", None)
        else:
            env["PM_AI_DISABLE_ENCRYPTION"] = "1"
        return env

    def run(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*_console_script(), *argv], env=self.env, capture_output=True, text=True,
            timeout=60, cwd=self.root,
        )

    def start(self) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [*_console_script(), "daemon", "run"], env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=self.root,
        )

    def request(
        self, method: str, path: str, body: object | None = None, *, token: bool = True
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {self.token}"
            payload = None if body is None else json.dumps(body).encode()
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def rows(self) -> list[tuple[str, str, str | None]]:
        with sqlite3.connect(self.store) as db:
            return list(db.execute("SELECT id, state, result FROM requests ORDER BY created_at"))


@pytest.fixture
def home(tmp_path: Path) -> Home:
    root = tmp_path / "home"
    (root / ".pm-ai").mkdir(parents=True)
    port = _free_port()
    (root / ".pm-ai" / "config.toml").write_text(
        f'daemon_port = {port}\ndisplay_timezone = "UTC"\n'
    )
    return Home(root, port)


def _wait_listening(process: subprocess.Popen[str]) -> list[str]:
    # Read on a thread, not by `select` on the pipe, which misses a second line
    # already taken into the text buffer (4x).
    return announced_lines(process)


def _stop(process: subprocess.Popen[str], sig: int = signal.SIGINT) -> tuple[int, str, str]:
    process.send_signal(sig)
    try:
        out, err = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
        pytest.fail(f"the daemon did not stop: {err}")
    return process.returncode, out, err


@pytest.fixture
def running(home: Home):
    process = home.start()
    try:
        _wait_listening(process)
        yield home, process
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def _events(home: Home, job: str) -> list[dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", home.port, timeout=30)
    try:
        connection.request(
            "GET", f"/v1/jobs/{job}/events", headers={"Authorization": f"Bearer {home.token}"}
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("content-type", "").startswith("text/event-stream")
        text = response.read().decode()
    finally:
        connection.close()
    return [json.loads(frame.removeprefix("data: ")) for frame in text.split("\n\n") if frame]


def test_dashboard_is_acknowledged_with_a_job_whose_stream_ends_done_with_the_path(running):
    home, _ = running
    status, _, body = home.request("POST", "/v1/prompt", {"line": "dashboard"})
    assert status == 202
    answered = json.loads(body)
    job = answered["job_id"]
    assert answered == {"inline": False, "job_id": job}
    assert [row[0] for row in home.rows()] == [job], "the requests row exists"
    events = _events(home, job)
    assert events[-1]["state"] == "done"
    written = home.root / ".manager-ai" / "memory" / "daily_dashboard.md"
    assert events[-1]["result"] == {
        "output": f"the personal dashboard is written: {written}\n",
        "exit": 0,
    }
    assert written.is_file()
    status, _, body = home.request("GET", f"/v1/jobs/{job}")
    assert status == 200 and json.loads(body)["state"] == "done"


def test_config_show_answers_what_the_terminal_prints_and_the_console_shows_none(running):
    home, process = running
    status, _, body = home.request("POST", "/v1/prompt", {"line": "config show"})
    assert status == 200
    answered = json.loads(body)
    terminal = home.run("config", "show")
    assert terminal.returncode == 0
    assert answered == {"inline": True, "output": terminal.stdout, "exit": 0}
    code, out, err = _stop(process)
    assert code == 0
    assert "blended_hourly_rate" not in out and "blended_hourly_rate" not in err


def test_the_three_endpoints_refuse_without_the_token(running):
    home, _ = running
    for method, path in (
        ("POST", "/v1/prompt"),
        ("GET", "/v1/jobs/job_0000000000000000"),
        ("GET", "/v1/jobs/job_0000000000000000/events"),
    ):
        status, _, body = home.request(method, path, {"line": "help"}, token=False)
        assert status == 401 and json.loads(body) == {"detail": "a valid token is required"}
    assert home.rows() == []


def test_a_restart_fails_unfinished_rows_and_removes_old_ones(home):
    assert home.run("config", "show").returncode == 0  # the store exists
    now = datetime.now(timezone.utc)
    with sqlite3.connect(home.store) as db:
        for job, state, created in (
            ("job_running00000000", "running", now),
            ("job_old0000000000000", "done", now - timedelta(days=8)),
            ("job_recent000000000", "done", now - timedelta(days=1)),
        ):
            db.execute(
                "INSERT INTO requests VALUES (?, 'dashboard', 'personal', ?, NULL, ?, ?)",
                (job, state, created.isoformat(), created.isoformat()),
            )
    process = home.start()
    try:
        _wait_listening(process)
        rows = {job: (state, result) for job, state, result in home.rows()}
        assert "job_old0000000000000" not in rows
        assert rows["job_recent000000000"][0] == "done"
        state, result = rows["job_running00000000"]
        assert state == "failed"
        assert json.loads(result)["error"] == "the daemon stopped before this job finished"
        assert _stop(process)[0] == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_ctrl_c_right_after_a_job_is_acknowledged_exits_0_and_leaves_it_settled(running):
    home, process = running
    status, _, body = home.request("POST", "/v1/prompt", {"line": "dashboard"})
    assert status == 202
    code, _, _ = _stop(process, signal.SIGINT)
    assert code == 0
    ((job, state, result),) = home.rows()
    assert job == json.loads(body)["job_id"]
    assert state in ("done", "failed")
    if state == "failed":
        assert "the daemon stopped" in json.loads(result)["error"]


def test_with_no_key_enrolled_the_daemon_starts_and_says_what_it_refuses(home):
    home.encryption = True
    process = home.start()
    try:
        lines = _wait_listening(process)
        assert any(NO_KEY_ENROLLED in line for line in lines), lines
        status, _, _ = home.request("POST", "/v1/prompt", {"line": "help"})
        assert status == 200
        assert _stop(process)[0] == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_a_keychain_that_cannot_be_read_refuses_the_start_and_nothing_listens(home):
    home.encryption = True
    home.backend = "keyring.backends.fail.Keyring"
    finished = home.run("daemon", "run")
    assert finished.returncode == 3, finished
    assert "keychain" in finished.stderr.lower()
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", home.port)) != 0, "nothing listens"


def test_a_store_moved_past_the_daemons_version_stops_it_with_exit_3_naming_the_version(running):
    home, process = running
    with sqlite3.connect(home.store) as db:
        db.execute("UPDATE schema_version SET version = 2")
    status, headers, _ = home.request("POST", "/v1/prompt", {"line": "dashboard"})
    assert status == 503 and headers.get("retry-after") == "1"
    try:
        out, err = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
        pytest.fail(f"the daemon did not stop on the mismatch: {err}")
    assert process.returncode == 3, (out, err)
    assert "schema version 2" in err
    with sqlite3.connect(home.store) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone()[0] == 0, "nothing written"
    lines = [
        json.loads(line)
        for path in sorted((home.application / "logs").iterdir())
        for line in path.read_text().splitlines()
    ]
    assert any(line.get("event") == "schema-version-mismatch" and line.get("found") == 2 for line in lines)


# ── Review fixes: the stream's wiring, the stop, the console ─────────────────


class _Silent:
    """A TCP listener that accepts and never answers: a provider gone quiet."""

    def __init__(self) -> None:
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.port = self.server.getsockname()[1]
        self.accepted: list[socket.socket] = []
        self.server.settimeout(0.2)
        self._running = True
        import threading

        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self) -> None:
        while self._running:
            try:
                connection, _ = self.server.accept()
            except OSError:
                continue
            self.accepted.append(connection)

    def close(self) -> None:
        self._running = False
        for connection in self.accepted:
            connection.close()
        self.server.close()


def _enrol_quiet_gitlab(home: Home, port: int) -> None:
    from pm_ai.app.wiring import build
    from pm_ai.connectors.gitlab import HOST_KEY
    from pm_ai.core.connector_enrolment import enrol_connector
    from pm_ai.core.project_registry import ProjectEntry, render_registry
    from pm_ai.domain.identity import DataScope, ScopeKind
    from pm_ai.platform.paths import ScopePaths

    project = home.root / "alpha"
    project.mkdir()
    (home.application / "projects.toml").write_bytes(
        render_registry({"alpha": ProjectEntry(path=project)})
    )
    daemon = build(
        None,
        DataScope(ScopeKind.APPLICATION),
        watched=["alpha"],
        paths=ScopePaths.production(home=home.root, projects={"alpha": project}),
        encryption_disabled=True,
    )
    try:
        enrol_connector(
            daemon.storage, system="gitlab", instance="gitlab:alpha",
            credential="glpat-0123456789abcdef", probe=lambda system, credential: "accepted",
            settings={HOST_KEY: f"https://localhost:{port}"}, project="group/project",
        )
    finally:
        daemon.storage.close()


def _open_stream(home: Home, job: str) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    connection = http.client.HTTPConnection("127.0.0.1", home.port, timeout=30)
    connection.request("GET", f"/v1/jobs/{job}/events", headers={"Authorization": f"Bearer {home.token}"})
    return connection, connection.getresponse()


def _next_event(response: http.client.HTTPResponse) -> dict[str, object] | None:
    lines: list[bytes] = []
    while True:
        line = response.readline()
        if not line:
            return None
        if line in (b"\n", b"\r\n"):
            if lines:
                return json.loads(b"".join(lines).decode().removeprefix("data: "))
            continue
        lines.append(line.rstrip(b"\r\n"))


def test_a_stream_on_a_job_in_flight_sees_its_later_states_arrive(home):
    quiet = _Silent()
    _enrol_quiet_gitlab(home, quiet.port)
    process = home.start()
    try:
        _wait_listening(process)
        status, _, body = home.request("POST", "/v1/prompt", {"line": "connector check"})
        assert status == 202
        job = json.loads(body)["job_id"]
        connection, response = _open_stream(home, job)
        first = _next_event(response)
        assert first is not None and first["state"] in ("queued", "running")
        quiet.close()  # the provider hangs up: the probe answers, the job ends
        states = [first["state"]]
        while (event := _next_event(response)) is not None:
            states.append(event["state"])
        connection.close()
        assert states[-1] == "done", states
        assert len(states) >= 2, "a later state arrived on the open stream"
        assert _stop(process)[0] == 0
    finally:
        quiet.close()
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_ctrl_c_with_a_stream_open_stops_promptly(home):
    quiet = _Silent()
    _enrol_quiet_gitlab(home, quiet.port)
    process = home.start()
    try:
        _wait_listening(process)
        _, _, body = home.request("POST", "/v1/prompt", {"line": "connector check"})
        connection, response = _open_stream(home, json.loads(body)["job_id"])
        assert _next_event(response) is not None
        started = time.monotonic()
        code, _, _ = _stop(process, signal.SIGINT)
        assert code == 0
        assert time.monotonic() - started < 5, "the open stream did not hold the stop"
        connection.close()
    finally:
        quiet.close()
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_an_unbuildable_row_is_warned_about_once_at_start_not_per_line(home):
    (home.application / "connectors").mkdir(parents=True, exist_ok=True)
    (home.application / "connectors" / "graph:broken.json").write_text(
        json.dumps({"instance": "graph:broken", "system": "graph", "enabled": True})
    )
    process = home.start()
    try:
        _wait_listening(process)
        for _ in range(3):
            assert home.request("POST", "/v1/prompt", {"line": "config show"})[0] == 200
        code, _, err = _stop(process)
        assert code == 0
        assert err.count("graph:broken") == 1, err
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_doctor_through_the_daemon_answers_what_the_terminal_does(running):
    import re

    home, _ = running
    status, _, body = home.request("POST", "/v1/prompt", {"line": "doctor"})
    assert status == 202
    job = json.loads(body)["job_id"]
    events = _events(home, job)
    result = events[-1]["result"]
    terminal = home.run("doctor")

    def comparable(text: str) -> list[str]:
        # The daemon line's uptime moves between the two runs; the selection
        # line is where the terminal stands, which a line sent through the
        # daemon has no part in.
        lines = [re.sub(r"up \d+s", "up Ns", line) for line in text.splitlines()]
        return [line for line in lines if "project selection" not in line]

    assert result["exit"] == terminal.returncode
    assert comparable(result["output"]) == comparable(terminal.stdout + terminal.stderr)


def test_with_no_key_enrolled_the_state_is_in_the_diagnostic_log(home):
    home.encryption = True
    process = home.start()
    try:
        _wait_listening(process)
        assert _stop(process)[0] == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    lines = [
        json.loads(line)
        for path in sorted((home.application / "logs").iterdir())
        for line in path.read_text().splitlines()
    ]
    assert any(NO_KEY_ENROLLED in str(line.get("message")) for line in lines)
