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
from pm_ai.app.wiring import build
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
probe = wiring.local_model_check("qwen3:8b").probe
assert probe.health is Health.WARNING, probe
assert "is not installed" in probe.detail and "runtime extra" in probe.detail, probe.detail
unset = wiring.local_model_check("").probe
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
