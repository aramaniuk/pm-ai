"""The conftest guard against the real pm-ai home survives a test's own undo."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pm_ai.platform.paths import ScopePaths


def test_the_guard_holds_after_monkeypatch_undo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test calling `monkeypatch.undo()` midway once reached the real home (story 15b's build)."""
    monkeypatch.setenv("PM_AI_UNUSED", "1")
    monkeypatch.undo()
    real_home = Path(os.path.expanduser("~"))
    with pytest.raises(pytest.fail.Exception, match="real home"):
        ScopePaths.production(home=real_home)
