"""The one shared restart sentence (AD-5, story 4r).

`project add` and `key enrol` print it every time they succeed, wherever they
run: a daemon already running read the enrolled projects and the master key at
its start, so neither change reaches it until it is restarted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pm_ai.domain.health import Report
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import KeyAlreadyEnrolled, KeyNotFound
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, RESTART_SENTENCE, dispatch


def test_the_sentence_is_the_one_the_spine_names():
    assert RESTART_SENTENCE == (
        "A daemon already running sees this after it is restarted (`pm-ai daemon run`)."
    )


@dataclass(frozen=True)
class _Onboarded:
    project_id: str
    repository: Path
    gitignore: Path
    already_registered: bool


class _Keychain:
    def __init__(self, held: bytes | None = None) -> None:
        self.held = held

    def store(self, name: str, secret: bytes) -> None:
        self.held = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if self.held is not None:
            raise KeyAlreadyEnrolled(name)
        self.held = secret

    def fetch(self, name: str) -> bytes:
        if self.held is None:
            raise KeyNotFound(name)
        return self.held

    def delete(self, name: str) -> None:
        self.held = None


@dataclass
class _Daemon:
    keychain: _Keychain
    storage: object = None
    config: object = None
    scope: DataScope = DataScope(ScopeKind.APPLICATION)


def _run(argv: list[str], **kwargs: object) -> int:
    return dispatch(argv, daemon=None, diagnose=lambda: Report(()), probe_connectors=None, **kwargs)


def test_project_add_prints_it_every_time_it_enrols(capsys, tmp_path):
    for _ in range(2):
        code = _run(
            ["project", "add", str(tmp_path)],
            onboard=lambda path, alias: _Onboarded("alpha", tmp_path, tmp_path / ".gitignore", False),
        )
        assert code == EXIT_OK
        assert capsys.readouterr().out.splitlines()[-1] == RESTART_SENTENCE


def test_project_add_already_enrolled_still_says_it(capsys, tmp_path):
    code = _run(
        ["project", "add", str(tmp_path)],
        onboard=lambda path, alias: _Onboarded("alpha", tmp_path, tmp_path / ".gitignore", True),
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "nothing changed" in out and out.splitlines()[-1] == RESTART_SENTENCE


def test_key_enrol_prints_it_when_it_enrols_and_not_when_refused(capsys):
    keychain = _Keychain()
    assert dispatch(
        ["key", "enrol"], daemon=_Daemon(keychain), diagnose=lambda: Report(()), probe_connectors=None
    ) == EXIT_OK
    assert capsys.readouterr().out.splitlines()[-1] == RESTART_SENTENCE
    assert dispatch(
        ["key", "enrol"], daemon=_Daemon(keychain), diagnose=lambda: Report(()), probe_connectors=None
    ) == EXIT_REFUSAL
    assert RESTART_SENTENCE not in capsys.readouterr().out
