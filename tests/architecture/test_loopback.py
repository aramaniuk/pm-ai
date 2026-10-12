"""AD-2 and AD-8 on the daemon's listener — story 4e.

AD-2 says the daemon binds strictly to `127.0.0.1`, with zero public listening
ports, ever. Two checks: the address never appears in the package as a
wildcard, and the socket the daemon actually serves on is bound to loopback.
"""

from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from pm_ai.app import daemon
from tests.architecture.conftest import PACKAGE_ROOT, REPO_ROOT

WILDCARD = "0.0.0" + ".0"  # spelled in two halves so this file does not match itself


def test_ad2_no_wildcard_address_appears_anywhere_in_the_package():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in PACKAGE_ROOT.rglob("*.py")
        if WILDCARD in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"AD-2: a wildcard bind address appears in {offenders}"


def test_ad2_the_daemon_listener_is_bound_to_loopback_only():
    assert daemon.LOOPBACK == "127.0.0.1"
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    listener = daemon.bind_loopback(port)
    try:
        assert listener.getsockname() == ("127.0.0.1", port)
        assert listener.family is socket.AF_INET
    finally:
        listener.close()


@pytest.mark.parametrize("module", ["starlette", "fastapi", "uvicorn"])
def test_the_suite_refuses_to_collect_without_the_runtime_extra(module):
    """No skip that reads as coverage: a missing extra module stops collection, naming the fix."""
    blocked = (
        f"import sys, runpy; sys.modules[{module!r}] = None; "
        f"runpy.run_path({str(REPO_ROOT / 'tests' / 'conftest.py')!r})"
    )
    result = subprocess.run(
        [sys.executable, "-c", blocked], capture_output=True, text=True, timeout=60
    )
    assert result.returncode != 0
    assert "uv sync --extra runtime" in result.stderr
    assert f"`{module}`" in result.stderr
