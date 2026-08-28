"""Runs mypy as part of the normal test run.

Same argument as `test_layering.py`, applied to a different class of invariant:
one command checks every architectural rule, rather than type checking being a
separate step someone forgets to wire into CI. There is no CI here, no
pre-commit and no Makefile, so a checker that has to be remembered is a checker
that runs until the first busy week.

What it protects, concretely: every `Protocol` in `pm_ai/ports/` is a contract
that nothing else verifies. The `@runtime_checkable` `isinstance` tests confirm
an attribute *exists* and stop there — not its signature, not its keyword names,
not its return type. Two real defects were found by turning mypy on for the
first time, and both were of exactly that shape: `pm_ai.storage` calling a method
`ScopePathPort` never declared, and the skill registry — the class enforcing
AD-18 and AD-20 — holding its skills as `object`, so every permission check read
an unverified attribute.

A missing `mypy` binary is a **failure**, never a skip. It is a declared dev
dependency, so its absence is a broken environment rather than an optional
feature, and a skip would report green while the check did not run. That lesson
is the one `test_layering.py` already carries.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from conftest import REPO_ROOT


def test_type_contracts_hold():
    """AD-30 and every port protocol — see `[tool.mypy]` in pyproject.toml."""
    if shutil.which("mypy") is None:
        pytest.fail(
            "mypy is not on PATH, so the type contracts did not run. It is a "
            "declared dev dependency (pyproject.toml `dev`), which makes its "
            "absence a broken environment rather than an optional feature. This "
            "fails rather than skips because a skip reports green while every "
            "port protocol goes unverified. Use `uv run pytest`, which puts "
            ".venv/bin on PATH, or `uv sync`."
        )

    # No arguments: `files = ["pm_ai"]` in pyproject.toml is the single
    # definition of what gets checked, so this cannot drift from `uv run mypy`.
    # The suite itself is deliberately excluded — it builds wrong values on
    # purpose to prove the guards reject them.
    result = subprocess.run(
        ["mypy"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Type contracts broken — an adapter has drifted from its port, or a "
        "consumer calls something no contract declares:\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
