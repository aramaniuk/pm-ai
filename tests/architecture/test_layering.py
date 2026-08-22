"""Runs the import-linter contracts as part of the normal test run.

Keeping this in pytest means one command checks every architectural invariant,
rather than layering being a separate step someone forgets to wire into CI.

A missing `lint-imports` binary is a **failure**, never a skip. The binary lives
in `.venv/bin`, so any invocation that does not put it on PATH would otherwise
turn the layering contract into a silent skip inside an otherwise-green run —
the one outcome this file exists to make impossible.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from conftest import PACKAGE_ROOT, REPO_ROOT


def test_import_contracts_hold():
    """Design Paradigm + AD-1, AD-5, AD-7, AD-15, AD-16, AD-26 — see .importlinter."""
    if not PACKAGE_ROOT.is_dir():
        pytest.skip("pm_ai/ does not exist yet (Phase 1)")
    if shutil.which("lint-imports") is None:
        pytest.fail(
            "import-linter is not on PATH, so the layering contracts did not run. "
            "It is a declared dev dependency (pyproject.toml `dev`), which makes "
            "its absence a broken environment rather than an optional feature. "
            "This fails rather than skips because a skip reports green while the "
            "one check every AD-30 boundary rests on is silently not executing — "
            "and a green run that proved nothing is worse than a red one. Use "
            "`uv run pytest`, which puts .venv/bin on PATH, or `uv sync --dev`."
        )

    result = subprocess.run(
        ["lint-imports", "--config", str(REPO_ROOT / ".importlinter")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Import contracts broken — a dependency points the wrong way:\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
