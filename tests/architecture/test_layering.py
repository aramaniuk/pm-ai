"""Runs the import-linter contracts as part of the normal test run.

Keeping this in pytest means one command checks every architectural invariant,
rather than layering being a separate step someone forgets to wire into CI.
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
        pytest.skip("import-linter not installed: uv add --dev import-linter")

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
