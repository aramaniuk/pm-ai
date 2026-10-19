"""Story 4w — `pm-ai logs` through `main()`, the prompt, the daemon and a real process.

Spec: `_bmad-output/specs/spec-pm-ai/stories/4w-pm-ai-logs.md`.

The in-process rows run `entry.main` against a throwaway `HOME` with the
keychain adapter replaced by one that fails the test if it is built, and
compare the whole home before and after. The rows about a real daemon start
the console script as separate processes, the way `test_daemon_slice` does.
"""

from __future__ import annotations

import json
import os
import select
import shlex
import signal
import subprocess
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import test_daemon_slice as daemon_slice
from test_line_answered_or_acknowledged import rig  # noqa: F401 — the daemon fixture
from pm_ai.app import entry
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR
from pm_ai.surfaces.cli import logs, repl
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, EXIT_USAGE

START = datetime(2026, 10, 18, 7, 0, 0, tzinfo=timezone.utc)


def record(n: int, *, logger: str = "pm_ai.api.requests", level: str = "info",
           at: datetime | None = None, **extra: object) -> bytes:
    line = {
        "at": (at or START + timedelta(seconds=n)).isoformat(),
        "logger": logger,
        "level": level,
        "message": f"GET /v1/status {n}",
        **extra,
    }
    return (json.dumps(line) + "\n").encode()


class _NoKeychain:
    """Built only if `logs` reached the keychain, which it must not."""

    def __init__(self) -> None:
        raise AssertionError("pm-ai logs built the keychain adapter")


def snapshot(root: Path) -> dict[str, tuple[int, int, bytes | None]]:
    """Every path under `root` with its mode, mtime and (for files) bytes."""
    seen: dict[str, tuple[int, int, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        status = path.lstat()
        content = None
        if path.is_file() and status.st_mode & 0o400:
            try:
                content = path.read_bytes()
            except PermissionError:
                content = None
        seen[str(path.relative_to(root))] = (status.st_mode, status.st_mtime_ns, content)
    return seen


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", _NoKeychain)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return root


def write_log(home: Path, payload: bytes, suffix: str = "") -> Path:
    path = home / ".pm-ai" / "logs" / f"daemon.log{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


# ── In process, through `main()` ─────────────────────────────────────────────


def test_no_daemon_and_broken_settings_print_the_log_and_compose_nothing(home, capsys):
    (home / ".pm-ai").mkdir()
    config = home / ".pm-ai" / "config.toml"
    config.write_text("daemon_port = [this is not toml\n")
    config.chmod(0)
    write_log(home, b"".join(record(n) for n in range(300, 400)), ".1")
    write_log(home, b"".join(record(n) for n in range(400, 600)))
    before = snapshot(home)
    try:
        code = entry.main(["logs"])
        after = snapshot(home)
    finally:
        config.chmod(0o600)
    seen = capsys.readouterr()
    assert after == before, "the home is unchanged"
    assert code == EXIT_OK
    lines = seen.out.splitlines()
    assert len(lines) == 50
    assert lines[-1].endswith("GET /v1/status 599") and "  request  " in lines[-1]
    assert seen.err.splitlines()[0].startswith(
        "pm-ai: reading ~/.pm-ai/logs/daemon.log (and daemon.log.1), times in "
    )


def test_a_home_with_no_pm_ai_gains_nothing(home, capsys):
    assert entry.main(["logs"]) == EXIT_OK
    assert list(home.iterdir()) == []
    seen = capsys.readouterr()
    assert seen.out == ""
    assert "~/.pm-ai/logs/daemon.log" in seen.err and "`pm-ai daemon run`" in seen.err


def test_an_unreadable_current_file_is_exit_3_naming_it(home, capsys):
    path = write_log(home, record(1))
    path.chmod(0)
    try:
        assert entry.main(["logs"]) == EXIT_REFUSAL
    finally:
        path.chmod(0o600)
    assert capsys.readouterr().err.strip() == (
        "pm-ai: the daemon log ~/.pm-ai/logs/daemon.log could not be read (Permission denied)."
    )


@pytest.mark.parametrize(
    "argv", [["logs", "--lines", "0"], ["logs", "--lines", "x"], ["logs", "--lines", "５"],
             ["logs", "--level", "loud"], ["logs", "--follow=1"], ["logs", "extra"]]
)
def test_a_command_line_pm_ai_cannot_read_is_exit_2(home, argv, capsys):
    assert entry.main(argv) == EXIT_USAGE
    assert not (home / ".pm-ai").exists()


def test_ctrl_c_while_reading_without_follow_is_mains_interruption(home, monkeypatch, capsys):
    write_log(home, record(1))

    def interrupted(*_: object, **__: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(logs, "format_record", interrupted)
    assert entry.main(["logs"]) == EXIT_REFUSAL
    assert "interrupted" in capsys.readouterr().err


def test_ctrl_c_while_following_is_exit_0(home, monkeypatch, capsys):
    write_log(home, record(1))
    looks = iter([lambda: write_log(home, record(1) + record(2))])

    def sleep(_: float) -> None:
        step = next(looks, None)
        if step is None:
            raise KeyboardInterrupt
        step()

    monkeypatch.setattr(logs, "_sleep", sleep)
    assert entry.main(["logs", "--follow"]) == EXIT_OK
    assert [line.rsplit(" ", 1)[1] for line in capsys.readouterr().out.splitlines()] == ["1", "2"]


# ── At the prompt, and sent to the daemon ────────────────────────────────────


class _NoDaemon:
    """A daemon client that fails the test if the prompt sends it anything."""

    def send(self, line: str):
        raise AssertionError(f"the prompt sent {line!r} to the daemon")

    def follow(self, job_id: str):
        raise AssertionError("the prompt followed a job")


def test_logs_follow_at_the_prompt_runs_here_and_ctrl_c_returns_with_exit_0(
    home, monkeypatch, capsys
):
    write_log(home, record(1))
    monkeypatch.setattr(logs, "_sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    typed = iter(["logs --follow", "config show", "exit"])
    ran: list[list[str]] = []

    def runner(words):
        ran.append(list(words))
        if words[0] == "logs":
            return entry.main(list(words))
        return EXIT_OK

    assert repl.run(runner, read=lambda prompt: next(typed), client=_NoDaemon()) == EXIT_OK
    assert ran[0] == ["logs", "--follow"]
    out = capsys.readouterr().out
    assert "GET /v1/status 1" in out
    assert "exit 0" in out.split("GET /v1/status 1", 1)[1], "Ctrl-C returned with exit 0"


def test_a_logs_line_sent_to_the_daemon_is_refused_by_name_as_terminal_bound(rig):  # noqa: F811
    for line in ("logs", "logs --follow", "logs --lines 5"):
        reply = rig.ask(line)
        assert reply.body["exit"] == EXIT_REFUSAL
        assert "`pm-ai logs`" in reply.body["output"]
        assert "holds the terminal until Ctrl-C" in reply.body["output"]


# ── A real process ───────────────────────────────────────────────────────────


def _env(home: Path, **extra: str) -> dict[str, str]:
    return {
        **os.environ,
        "HOME": str(home),
        "PM_AI_DISABLE_ENCRYPTION": "1",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        **extra,
    }


def _script() -> str:
    return shlex.join(daemon_slice._console_script())  # noqa: SLF001


def test_piped_into_head_prints_three_lines_and_no_broken_pipe_message(tmp_path):
    home = tmp_path / "home"
    write_log(home, b"".join(record(n) for n in range(10_000)))
    shell = subprocess.run(
        ["/bin/bash", "-c", f"set -o pipefail; {_script()} logs --lines 10000 | head -3"],
        env=_env(home), capture_output=True, text=True, timeout=60,
    )
    assert shell.returncode == 0, shell.stderr
    assert len(shell.stdout.splitlines()) == 3
    assert "Broken pipe" not in shell.stderr and "Exception" not in shell.stderr
    assert shell.stderr.startswith("pm-ai: reading ~/.pm-ai/logs/daemon.log")


def test_follow_into_head_ends_with_exit_0_and_no_message_when_lines_come_after(tmp_path):
    """Lines written after `head` left: no "Exception ignored … BrokenPipeError" at exit."""
    home = tmp_path / "home"
    path = write_log(home, record(1))
    follower = subprocess.Popen(
        ["/bin/bash", "-c", f"set -o pipefail; {_script()} logs --follow | head -1"],
        env=_env(home, TZ="UTC"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(1.5)
    with path.open("ab") as log:
        log.write(record(2) + record(3))
    try:
        out, err = follower.communicate(timeout=30)
    finally:
        if follower.poll() is None:
            follower.kill()
    assert follower.returncode == 0, err
    assert out.decode().splitlines() == ["2026-10-18 07:00:01  info  request  GET /v1/status 1"]
    assert b"Exception" not in err and b"Broken pipe" not in err, err


def test_the_machine_zone_is_read_per_record_across_daylight_saving(tmp_path):
    home = tmp_path / "home"
    write_log(
        home,
        record(1, at=datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc))
        + record(2, at=datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)),
    )
    ran = subprocess.run(
        [*daemon_slice._console_script(), "logs"],  # noqa: SLF001
        env=_env(home, TZ="Europe/Berlin"), capture_output=True, text=True, timeout=60,
    )
    assert ran.returncode == 0, ran.stderr
    assert [line[:19] for line in ran.stdout.splitlines()] == [
        "2026-10-25 02:30:00", "2026-10-25 02:30:00"
    ]
    assert "times in Europe/Berlin (UTC+0" in ran.stderr.splitlines()[0]


def test_after_a_daemon_answered_requests_and_stopped_logs_shows_them_in_local_time(
    tmp_path,
):
    """AC: request lines in local time with `request` as their source; exit 0."""
    home = daemon_slice.Home(tmp_path / "home", daemon_slice._free_port())  # noqa: SLF001
    (home.application).mkdir(parents=True)
    (home.application / "config.toml").write_text(f"daemon_port = {home.port}\n")
    process = home.start()
    try:
        daemon_slice._wait_listening(process)  # noqa: SLF001
        token = home.token_file.read_text()
        home.get("/v1/status")
        home.get("/v1/status", token)
    finally:
        code, _, _ = daemon_slice._stop(process)  # noqa: SLF001
    assert code == 0
    written = [
        line for line in home.log_lines() if line["logger"] == "pm_ai.api.requests"
    ]
    ran = subprocess.run(
        [*daemon_slice._console_script(), "logs"],  # noqa: SLF001
        env=_env(home.root, TZ="Asia/Tokyo"), capture_output=True, text=True, timeout=60,
    )
    assert ran.returncode == 0, ran.stderr
    assert ran.stderr.splitlines()[0] == (
        "pm-ai: reading ~/.pm-ai/logs/daemon.log, times in Asia/Tokyo (UTC+09:00 now)"
    )
    shown = [line for line in ran.stdout.splitlines() if "  request  " in line]
    tokyo = ZoneInfo("Asia/Tokyo")
    assert shown == [
        f"{datetime.fromisoformat(str(line['at'])).astimezone(tokyo):%Y-%m-%d %H:%M:%S}"
        f"  info  request  {line['message']}"
        for line in written
    ]
    assert [line.rsplit("  ", 1)[1] for line in shown] == [
        "GET /v1/status 401", "GET /v1/status 200"
    ]


class _LineReader:
    """Whole lines from a pipe's raw descriptor, never blocking past a deadline.

    `select` on the descriptor and `os.read` of what is there, split here: a
    buffered text stream can hold lines `select` cannot see, and its
    `readline()` blocks on a line not yet finished.
    """

    def __init__(self, stream) -> None:
        self.fd = stream.fileno()
        self.pending = b""
        self.closed = False

    def line(self, deadline: float) -> str | None:
        while b"\n" not in self.pending:
            left = deadline - time.monotonic()
            if left <= 0 or self.closed:
                return None
            ready, _, _ = select.select([self.fd], [], [], min(left, 0.1))
            if ready:
                chunk = os.read(self.fd, 65536)
                if not chunk:
                    self.closed = True
                self.pending += chunk
        line, _, self.pending = self.pending.partition(b"\n")
        return line.decode("utf-8") + "\n"


def test_follow_with_no_file_then_the_daemon_starts_shows_its_lines_within_a_second(
    tmp_path,
):
    """AC: the no-file sentence, then the daemon's lines as written; Ctrl-C exits 0."""
    home = daemon_slice.Home(tmp_path / "home", daemon_slice._free_port())  # noqa: SLF001
    (home.application).mkdir(parents=True)
    (home.application / "config.toml").write_text(f"daemon_port = {home.port}\n")
    follower = subprocess.Popen(
        [*daemon_slice._console_script(), "logs", "--follow"],  # noqa: SLF001
        env=_env(home.root, TZ="UTC"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert follower.stdout is not None and follower.stderr is not None
    out, err = _LineReader(follower.stdout), _LineReader(follower.stderr)
    daemon: subprocess.Popen[str] | None = None
    try:
        first = err.line(time.monotonic() + 30)
        assert first is not None and first.startswith("pm-ai: there is no daemon log yet"), first
        daemon = home.start()
        daemon_slice._wait_listening(daemon)  # noqa: SLF001
        home.get("/v1/status")
        deadline = time.monotonic() + 15
        arrivals: list[tuple[str, datetime]] = []
        while time.monotonic() < deadline:
            line = out.line(deadline)
            if line is None:
                break
            arrivals.append((line.rstrip("\n"), datetime.now(timezone.utc)))
            if line.rstrip().endswith("GET /v1/status 401"):
                break
        assert arrivals and arrivals[-1][0].endswith("  info  request  GET /v1/status 401")
        written = [
            entry_ for entry_ in home.log_lines()
            if entry_["message"] == "GET /v1/status 401"
        ]
        at = datetime.fromisoformat(str(written[0]["at"]))
        lag = (arrivals[-1][1] - at).total_seconds()
        assert lag < 1.0, f"the line arrived {lag:.2f}s after it was written"
        follower.send_signal(signal.SIGINT)
        assert follower.wait(timeout=15) == 0
    finally:
        if daemon is not None and daemon.poll() is None:
            daemon_slice._stop(daemon)  # noqa: SLF001
        if follower.poll() is None:
            follower.kill()
        follower.communicate()
