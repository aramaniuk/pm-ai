"""From `projects.toml` on disk to a daemon with a project scope (story 4d).

The condition every other wave-1 slice assumed and none established. Before
this, `_registered_projects` returned `{}` unconditionally, so `doctor` was the
only subcommand that could run on any machine — and the whole path from a
registry file, through `ScopePaths.production`, into `build()`, was exercised by
nothing.

Nothing is stubbed here but the keychain, which would otherwise reach the
developer's login keychain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pm_ai.app import entry
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.domain.health import Health, Presence
from pm_ai.platform.doctor import registry_readable
from pm_ai.ports import KeyNotFound


class Keychain:
    """Enough of a `KeychainPort` to compose, holding one key."""

    def __init__(self, secret: bytes | None = b"\x00" * 32) -> None:
        self._secret = secret

    def store(self, name: str, secret: bytes) -> None:
        self._secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if self._secret is None:
            self._secret = secret

    def fetch(self, name: str) -> bytes:
        if self._secret is None:
            raise KeyNotFound(name)
        return self._secret

    def delete(self, name: str) -> None:
        self._secret = None


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A home with a `.pm-ai` directory and a repository beside it, nothing else."""
    home = tmp_path / "home"
    (home / ".pm-ai").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    repository = tmp_path / "alpha"
    repository.mkdir()
    return home, repository


def _write(home: Path, **projects: Path) -> None:
    (home / ".pm-ai" / "projects.toml").write_bytes(
        render_registry({name: ProjectEntry(path=path) for name, path in projects.items()})
    )


def test_an_onboarded_project_composes_a_daemon_whose_scope_resolves(machine):
    """The whole point of the slice, asserted end to end.

    `scope_root` is what `build()` resolves eagerly, and an unregistered project
    is what made it raise. That it resolves here is the condition `4k`, `4h` and
    every harvest slice are waiting on.
    """
    home, repository = machine
    _write(home, alpha=repository)

    composed = entry._compose(Keychain())

    assert composed.failure is None, composed.failure
    assert composed.daemon is not None
    assert composed.daemon.storage is not None
    assert composed.registry.presence is Presence.READ


def test_the_registry_the_writer_renders_is_the_one_the_reader_reads(machine):
    """Render, write through a real file, read back through the real reader.

    The round trip is unit-tested over bytes; this is the same guarantee with
    the filesystem and `ScopePaths` in the middle, which is where a byte-perfect
    serializer can still be written to the wrong path.
    """
    home, repository = machine
    _write(home, alpha=repository)

    assert entry._bootstrap(Keychain()).projects == {"alpha": ProjectEntry(path=repository)}


def test_an_unreadable_registry_is_reported_and_never_raises(machine):
    """A directory where `projects.toml` should be — the row `4d` would not
    propagate.

    Composition returns a probe rather than an exception, and the probe says
    "could not be read" rather than "no project is enrolled". Reported as
    absence, this machine would be told to run `pm-ai project add`, and that
    command's write would be the thing that destroyed the registry it could not
    open.
    """
    home, _repository = machine
    (home / ".pm-ai" / "projects.toml").mkdir()

    composed = entry._compose(Keychain())

    assert composed.daemon is None
    assert composed.registry.presence is Presence.UNREADABLE
    assert composed.failure is not None
    assert composed.failure.health is Health.FAILING
    assert "could not be read" in composed.failure.detail


def test_a_malformed_registry_is_reported_with_the_parsers_own_message(machine):
    home, _repository = machine
    (home / ".pm-ai" / "projects.toml").write_text("[projects.alpha]\npath = 7\n")

    composed = entry._compose(Keychain())

    assert composed.daemon is None
    assert composed.failure is not None
    assert composed.failure.health is Health.FAILING
    assert "alpha" in composed.failure.detail


def test_a_registry_naming_a_vanished_repository_still_composes_but_doctor_says_so(
    machine,
):
    """Reporting without refusing, which is what row 7 was reduced to.

    `ScopePaths.repository()` performs no existence check, so the daemon builds
    — and that is deliberate rather than overlooked: `doctor` is where the
    operator learns, and a resolver that stat-ed the filesystem would be doing
    I/O in the one module that does none.
    """
    home, repository = machine
    _write(home, alpha=repository)
    repository.rmdir()

    composed = entry._compose(Keychain())
    probe = registry_readable(composed.registry)

    assert probe.health is Health.FAILING
    assert "alpha" in probe.detail
    assert "re-enrol" in probe.remediation.lower()
