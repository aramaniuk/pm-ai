"""Story 7a in the daemon: the local model is wired at composition, and its pool
starts with the I/O pool and stops before the writer drains (AD-19, AD-5).

Nothing here contacts Ollama: the adapter builds its client on first use, and
these tests never make a call.
"""

from __future__ import annotations

import socket
from dataclasses import replace
from pathlib import Path

import pytest

from test_line_answered_or_acknowledged import _NoKey
from pm_ai.app import concurrency, entry
from pm_ai.app import daemon as daemon_module
from pm_ai.app.prompts import RequestPath
from pm_ai.app.wiring import SealedRefreshTokenStore, build, refreshed
from pm_ai.core.config import Config
from pm_ai.domain.health import Report
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.models.local.ollama import OllamaModel
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR
from pm_ai.ports import DaemonRefused
from tests.slice.test_first_run import Keychain

APPLICATION = DataScope(ScopeKind.APPLICATION)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    (root / ".pm-ai").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)
    return root


def test_daemon_run_composes_the_named_local_model_and_contacts_nothing(home):
    """`daemon run` targets the application scope; its composition wires the model."""
    (home / ".pm-ai" / "config.toml").write_text('local_model = "qwen3:8b"\n')
    composed = entry._compose(Keychain(), APPLICATION)  # noqa: SLF001
    assert composed.daemon is not None, composed.failure
    model = composed.daemon.model
    assert isinstance(model, OllamaModel)
    assert model.name == "qwen3:8b"
    assert not model.opened, "composition builds no client and asks nothing"


def test_no_local_model_named_composes_no_adapter(home):
    composed = entry._compose(Keychain(), APPLICATION)  # noqa: SLF001
    assert composed.daemon is not None, composed.failure
    assert composed.daemon.model is None


class _Model:
    """Stands in for the adapter: records when the shutdown closes it."""

    def __init__(self, order: list[str]) -> None:
        self._order = order

    def close(self) -> None:
        self._order.append("model closed")


def test_the_model_pool_starts_with_the_io_pool_and_stops_before_the_writer_drains(
    tmp_path, monkeypatch
):
    order: list[str] = []
    daemon = build(tmp_path, APPLICATION, keychain=_NoKey(), encryption_disabled=True)
    daemon = replace(daemon, config=Config(daemon_port=_free_port()), model=_Model(order))  # type: ignore[arg-type]

    real_start = concurrency.ModelPool.start
    real_shutdown = concurrency.ModelPool.shutdown
    real_stop_writer = daemon_module._stop_writer  # noqa: SLF001

    # Story 7c: the frontier pool is a second pool of one, named apart.
    def which(pool) -> str:
        return "frontier pool" if pool._thread.name.startswith("pm-ai-frontier") else "model pool"  # noqa: SLF001

    def start(self):
        order.append(f"{which(self)} started")
        real_start(self)

    def shutdown(self):
        order.append(f"{which(self)} shutdown")
        real_shutdown(self)

    def stop_writer(requests):
        order.append("writer drains")
        return real_stop_writer(requests)

    def refuse(self):
        # Stop right after start-up: the stop sequence in `finally` runs.
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(concurrency.ModelPool, "start", start)
    monkeypatch.setattr(concurrency.ModelPool, "shutdown", shutdown)
    monkeypatch.setattr(daemon_module, "_stop_writer", stop_writer)
    monkeypatch.setattr(RequestPath, "restart_pass", refuse)
    with pytest.raises(DaemonRefused):
        daemon_module.serve(daemon, probes=lambda: Report(()))
    assert isinstance(daemon.model_pool, concurrency.ModelPool)
    assert order == [
        "model pool started",
        "frontier pool started",
        "model pool shutdown",
        "model closed",  # idle at the stop, and closed all the same (P10)
        "frontier pool shutdown",
        "writer drains",
    ]
    assert daemon.model_pool.stopping
    assert daemon.frontier_pool is not None and daemon.frontier_pool.stopping


_WITHOUT_HTTPX = """
import importlib.abc, sys

class _Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("httpx", "httpcore"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None

sys.meta_path.insert(0, _Absent())
from pm_ai.app import entry, wiring
from pm_ai.core.config import Config
from pm_ai.domain.health import Health

assert "pm_ai.models.local.ollama" not in sys.modules
assert "httpx" not in sys.modules
assert wiring.local_model(Config(local_model="qwen3:8b")) is None
probe = wiring.local_model_check("qwen3:8b", window=32768).probe
assert probe.health is Health.WARNING, probe
assert "is not installed" in probe.detail and "runtime extra" in probe.detail, probe.detail
unset = wiring.local_model_check("", window=32768).probe
assert unset.health is Health.OK and "none named" in unset.detail, unset
assert "pm_ai.models.local.ollama" not in sys.modules
print("composes without the runtime extra")
"""


def test_without_httpx_every_command_still_composes_and_the_line_says_why():
    """The adapter imports `httpx` (runtime extra); nothing imports it at module level."""
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-c", _WITHOUT_HTTPX],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert done.returncode == 0, done.stderr
    assert "composes without the runtime extra" in done.stdout


# ── Story 7g: the window is a setting the composition root passes on ─────────


@pytest.mark.parametrize(("written", "window"), [("", 32768), ("local_model_window = 16384\n", 16384)])
def test_the_composed_adapter_runs_at_the_configured_window(home, written, window):
    """Matrix rows "Unset" and "Lowered"."""
    (home / ".pm-ai" / "config.toml").write_text(f'local_model = "qwen3:8b"\n{written}')
    composed = entry._compose(Keychain(), APPLICATION)  # noqa: SLF001
    assert composed.daemon is not None, composed.failure
    assert isinstance(composed.daemon.model, OllamaModel)
    assert composed.daemon.model.window == window


@pytest.mark.parametrize("value", ["8192", "32768.0", '"32768"', "true"])
def test_another_window_stops_composition_naming_both_values(home, value):
    """Matrix row "Invalid": the daemon does not start."""
    (home / ".pm-ai" / "config.toml").write_text(
        f'local_model = "qwen3:8b"\nlocal_model_window = {value}\n'
    )
    composed = entry._compose(Keychain(), APPLICATION)  # noqa: SLF001
    assert composed.daemon is None
    assert "local_model_window" in str(composed.failure)
    assert "16384" in str(composed.failure) and "32768" in str(composed.failure)


def _asked(monkeypatch) -> list[tuple[str, int, bool]]:
    from pm_ai.domain.model_calls import LocalModelPresence, unset_local_model

    asked: list[tuple[str, int, bool]] = []

    def check(model: str, *, window: int, from_adapter: bool = False) -> LocalModelPresence:
        asked.append((model, window, from_adapter))
        return unset_local_model()

    monkeypatch.setattr(entry, "local_model_check", check)
    return asked


def test_doctor_asks_the_presence_check_at_the_configured_window(home, monkeypatch):
    """Matrix row "Doctor": composed with a model, the line names its adapter's window."""
    asked = _asked(monkeypatch)
    (home / ".pm-ai" / "config.toml").write_text(
        'local_model = "qwen3:8b"\nlocal_model_window = 16384\n'
    )
    composed = entry._compose(Keychain(), APPLICATION)  # noqa: SLF001
    entry._local_model_probe(composed)  # noqa: SLF001
    assert asked == [("qwen3:8b", 16384, True)]


def test_doctor_names_the_adapter_s_window_after_the_file_changed(home, monkeypatch):
    """A changed window waits for a restart, so doctor names the one in use."""
    asked = _asked(monkeypatch)
    (home / ".pm-ai" / "config.toml").write_text('local_model = "qwen3:8b"\n')
    composed = entry._compose(Keychain(), APPLICATION)  # noqa: SLF001
    (home / ".pm-ai" / "config.toml").write_text(
        'local_model = "qwen3:8b"\nlocal_model_window = 16384\n'
    )
    entry._local_model_probe(composed)  # noqa: SLF001
    assert asked == [("qwen3:8b", 32768, True)]


def test_doctor_with_no_adapter_names_the_file_s_window(home, monkeypatch):
    asked = _asked(monkeypatch)
    (home / ".pm-ai" / "config.toml").write_text("local_model_window = 16384\n")
    composed = entry._compose(Keychain(), APPLICATION)  # noqa: SLF001
    assert composed.daemon is not None and composed.daemon.model is None
    entry._local_model_probe(composed)  # noqa: SLF001
    assert asked == [("", 16384, False)]


def test_config_show_marks_a_lowered_window_set(home, monkeypatch, capsys):
    """Matrix row "Lowered": `config show` lists it, marked set."""
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: Keychain())
    (home / ".pm-ai" / "config.toml").write_text("local_model_window = 16384\n")
    assert entry.main(["config", "show"]) == 0
    (row,) = [
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith("local_model_window ")
    ]
    assert "16384" in row and "(set)" in row


def test_a_window_changed_while_running_is_kept_until_restart(home):
    """Matrix row "Changed while running": the daemon's adapter is built once, at start."""
    (home / ".pm-ai" / "config.toml").write_text('local_model = "qwen3:8b"\n')
    composed = entry._compose(Keychain(), APPLICATION)  # noqa: SLF001
    assert composed.daemon is not None
    model = composed.daemon.model
    (home / ".pm-ai" / "config.toml").write_text(
        'local_model = "qwen3:8b"\nlocal_model_window = 16384\n'
    )
    daemon = composed.daemon
    fresh = refreshed(
        daemon,
        store_for=lambda instance, system: SealedRefreshTokenStore(
            storage=daemon.storage, instance=instance, system=system
        ),
    )
    assert fresh.config.local_model_window == 16384, "the line reads the file again"
    assert fresh.model is model and model.window == 32768, "the adapter waits for a restart"


@pytest.mark.parametrize(("written", "window"), [("", 32768), ("local_model_window = 16384\n", 16384)])
def test_setup_asks_the_presence_check_at_the_window_the_file_names(home, monkeypatch, written, window):
    """Setup never sets the window, so its check is at the file's (review of 7g)."""
    from pm_ai.domain.model_calls import LocalModelPresence, unset_local_model

    asked: list[tuple[str, int]] = []

    def check(model: str, *, window: int, from_adapter: bool = False) -> LocalModelPresence:
        assert not from_adapter, "setup has no adapter: the file's window"
        asked.append((model, window))
        return unset_local_model()

    monkeypatch.setattr(entry, "local_model_check", check)
    if written:
        (home / ".pm-ai" / "config.toml").write_text(written)
    entry._FirstRun(Keychain()).local_model("qwen3:8b")  # noqa: SLF001
    assert asked == [("qwen3:8b", window)]
