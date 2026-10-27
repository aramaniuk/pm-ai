"""Story 1s — private files stay private, one test per row of the I/O matrix.

Every row sets a *real* process umask and restores it in `finally`
(`_umask`): monkeypatching `os.umask` changes nothing the kernel applies, so it
could not fail (story 1m). The write rows run the real single writer over a
redirected home and assert before any daemon start, so the start-up walk cannot
hide a write path that creates the wrong mode. The walk rows run the daemon's
own walk (`pm_ai.app.daemon.tighten_private_modes`) in-process, with its system
calls replaced where a row needs another owner, a filesystem that does not keep
a mode, or a path swapped mid-walk. Two rows start the real console script.
"""

from __future__ import annotations

import asyncio
import errno
import http.client
import itertools
import json
import logging
import os
import select
import signal
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ledger_fixtures import entry as _entry
from pm_ai.app import daemon as daemon_module
from pm_ai.app import entry
from pm_ai.app.concurrency import IOPool, WriterThread
from pm_ai.app.entry import DAEMON_BOUNDS
from pm_ai.app.pipelines import DASHBOARD_ARTIFACT, run_dashboard, run_harvest
from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
from pm_ai.app.ticks import HarvestTimers, RenderTimers
from pm_ai.app.wiring import Daemon, build, onboard_project
from pm_ai.core.config import Config
from pm_ai.core.connector_enrolment import enrol_connector
from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.domain.disclosure import DisclosureRecord
from pm_ai.domain.events import CommitPayload, NormalizedEvent, ObservedEventType
from pm_ai.domain.handles import person_handle
from pm_ai.domain.harvest import Cursor, HarvestOutcome, HarvestResult
from pm_ai.domain.health import Health, Probe, Report
from pm_ai.domain.identity import DataScope, ScopeKind, SourceRef
from pm_ai.domain.lifecycle import CoverageWindow
from pm_ai.domain.storage_tiers import CAPTURE_STAGING, EVENT_LOG
from pm_ai.domain.vcs import TrackingVerdict
from pm_ai.platform import doctor
from pm_ai.platform.claims import hold_for_life
from pm_ai.platform.environment import DISABLE_ENCRYPTION_VAR
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import KeyAlreadyEnrolled, KeyNotFound
from pm_ai.storage.service import ModeCalls, ModeWalk, StorageService

APPLICATION = DataScope(ScopeKind.APPLICATION)
PERSONAL = DataScope(ScopeKind.PERSONAL)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")
NOW = datetime(2026, 10, 17, 7, 0, tzinfo=timezone.utc)
ORDINARY = 0o022
OWNER_STRIPPING = 0o200


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


@contextmanager
def _umask(mask: int) -> Iterator[None]:
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def _not_private(root: Path) -> list[str]:
    """Every entry under `root` (itself included) that is not owner-only."""
    wrong: list[str] = []
    for current, folders, files in os.walk(root):
        here = Path(current)
        if _mode(here) != 0o700:
            wrong.append(f"{here} {_mode(here):04o}")
        for name in files:
            path = here / name
            if path.is_symlink():
                continue
            if _mode(path) & 0o077 or _mode(path) & 0o600 != 0o600:
                wrong.append(f"{path} {_mode(path):04o}")
    return wrong


class _Keychain:
    """Enough of a `KeychainPort` to compose and to enrol, holding one key."""

    def __init__(self) -> None:
        self.secret: bytes | None = None

    def store(self, name: str, secret: bytes) -> None:
        self.secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if self.secret is not None:
            raise KeyAlreadyEnrolled(name)
        self.secret = secret

    def fetch(self, name: str) -> bytes:
        if self.secret is None:
            raise KeyNotFound(name)
        return self.secret

    def delete(self, name: str) -> None:
        self.secret = None


class _Vcs:
    """A git that says every path inside the repository is excluded."""

    def __init__(self, repository: Path) -> None:
        self.repository = repository

    def working_tree(self, path: Path) -> Path | None:
        return self.repository if self.repository in path.parents else None

    def tracking(self, path: Path, *, repository: Path) -> TrackingVerdict:
        return TrackingVerdict(ignored=True)

    def repository_marker_above(self, path: Path) -> Path | None:  # pragma: no cover
        return None


class _Commits:
    """A connector filed under the project that answers one commit, then nothing."""

    system = "double"

    def __init__(self, instance: str = "gitlab:alpha") -> None:
        self.instance = instance
        self.name = instance
        self.scope = ALPHA

    def harvest(self, since: Cursor) -> HarvestResult:
        event = NormalizedEvent(
            scope=ALPHA,
            type=ObservedEventType.COMMIT_PUSHED,
            source_ref=SourceRef.parse("gitlab:alpha:commit:0000abcd"),
            actor=person_handle("email", "ada@example.com"),
            occurred_at=NOW - timedelta(hours=1),
            payload=CommitPayload(sha="0000abcd", message="change", branch="main"),
        )
        return HarvestResult(
            events=(event,),
            cursor=Cursor(b"after"),
            outcome=HarvestOutcome.HARVESTED,
            coverage=CoverageWindow(self.instance, NOW, NOW),
            answered_at=NOW,
        )

    def emits(self) -> frozenset[ObservedEventType]:
        return frozenset({ObservedEventType.COMMIT_PUSHED})

    def sample_events(self) -> tuple:
        return ()

    def check_health(self) -> Probe:
        return Probe(self.instance, Health.OK, "a double")


class Machine:
    """A redirected home, a project repository beside it, and a way to compose."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.repository = root / "work" / "alpha"

    @property
    def application(self) -> Path:
        return self.home / ".pm-ai"

    @property
    def personal(self) -> Path:
        return self.home / ".manager-ai"

    @property
    def project(self) -> Path:
        return self.repository / ".project-ai"

    def paths(self, *, projects: dict[str, Path] | None = None) -> ScopePaths:
        known = {"alpha": self.repository} if projects is None else projects
        return ScopePaths.production(home=self.home, projects=known)

    def daemon(self, *, projects: dict[str, Path] | None = None) -> Daemon:
        known = {"alpha": self.repository} if projects is None else projects
        repository = known.get("alpha", self.repository)
        return build(
            None,
            PERSONAL,
            watched=tuple(known),
            paths=self.paths(projects=known),
            keychain=_Keychain(),
            encryption_disabled=True,
            now=lambda: NOW,
            vcs=_Vcs(repository),
            # The dashboard reads the zone off the composed config; a real
            # start reads config.toml, which several rows widen by hand.
            config=Config(display_timezone="UTC"),
        )


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Machine]:
    built = Machine(tmp_path)
    built.home.mkdir()
    monkeypatch.setenv("HOME", str(built.home))
    monkeypatch.delenv(DISABLE_ENCRYPTION_VAR, raising=False)
    yield built
    # Put back whatever a row took away, so the temporary tree can be removed.
    for current, folders, _files in os.walk(tmp_path):
        for name in folders:
            path = Path(current) / name
            if not path.is_symlink():
                with _quiet():
                    path.chmod(_mode(path) | 0o700)


@contextmanager
def _quiet() -> Iterator[None]:
    try:
        yield
    except OSError:
        pass


def _walk(
    daemon: Daemon, *, calls: ModeCalls | None = None, budget: float = 30.0
) -> tuple[ModeWalk, list[str]]:
    lines: list[str] = []
    report = daemon_module.tighten_private_modes(
        daemon, announce=lines.append, calls=calls, budget=budget
    )
    return report, lines


def _close(daemon: Daemon) -> None:
    daemon.storage.close()


def _config(daemon: Daemon, body: bytes = b'display_timezone = "UTC"\n') -> Path:
    return daemon.storage.write_artifact(body, scope=APPLICATION, artifact="config.toml")


# ── A fresh machine, before any daemon start ─────────────────────────────────


class _Operator:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)

    def __call__(self, prompt: str = "") -> str:
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


def _setup(machine: Machine, monkeypatch: pytest.MonkeyPatch) -> None:
    """`pm-ai setup` through `entry.main`, enrolling `alpha` and writing config.toml."""
    keychain = _Keychain()
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: keychain)
    monkeypatch.setattr(doctor, "missing_distributions", lambda _names: ())
    monkeypatch.setattr(
        "builtins.input",
        _Operator(str(machine.repository), "alpha", "85", "UTC", "n", "n"),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    entry.main(["setup"])
    assert (machine.application / "projects.toml").is_file()
    assert (machine.application / "config.toml").is_file()


def test_a_fresh_machine_is_private_before_any_daemon_start(machine, monkeypatch):
    """Row "Fresh machine, umask 022": setup, both renders and one harvest, in-process."""
    with _umask(ORDINARY):
        _setup(machine, monkeypatch)
        daemon = machine.daemon()
        try:
            commits = _Commits()
            daemon.connectors = {commits.instance: commits}
            daemon.instance_scopes = {commits.instance: ALPHA}
            run_dashboard(daemon, scope=PERSONAL, now=NOW)
            run_dashboard(daemon, scope=ALPHA, now=NOW)
            assert run_harvest(daemon, commits.instance).persisted == 1
        finally:
            _close(daemon)

    assert _not_private(machine.application) == []
    assert _not_private(machine.personal) == []
    assert _not_private(machine.project / "memory") == []
    assert (machine.project / "memory" / "event_log").is_dir()


def test_the_personal_dashboard_and_its_folders_are_private(machine):
    """Row "Personal dashboard": `0644` inside `0755` in the field test."""
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            _config(daemon)
            written = run_dashboard(daemon, scope=PERSONAL, now=NOW)
        finally:
            _close(daemon)

    assert written.name == DASHBOARD_ARTIFACT
    assert _mode(written) == 0o600
    assert _mode(written.parent) == 0o700
    assert _mode(machine.personal) == 0o700


def test_a_projects_private_folders_are_private_and_its_shared_parts_keep_the_umask(machine):
    """Row "Project folders": project add, a render, then a transcript drop."""
    with _umask(ORDINARY):
        machine.repository.mkdir(parents=True)
        onboard_project(_Keychain(), str(machine.repository))
        daemon = machine.daemon()
        try:
            _config(daemon)
            dashboard = run_dashboard(daemon, scope=ALPHA, now=NOW)
            capture = daemon.storage.write_capture("hello", scope=ALPHA, name="standup.txt")
        finally:
            _close(daemon)

    project = machine.project
    for folder in ("memory", "transcripts", "transcripts/temp"):
        assert _mode(project / folder) == 0o700, folder
    assert _mode(dashboard) == 0o600
    assert _mode(capture) == 0o600
    assert _mode(project) == 0o755
    assert _mode(project / "rules") == 0o755
    assert _mode(project / "skills") == 0o755
    assert _mode(machine.repository / ".gitignore") == 0o644


def test_a_staged_capture_is_private_before_it_is_published(machine, monkeypatch):
    """Row "Capture staging": what an interruption after staging would leave."""
    seen: list[tuple[int, int]] = []
    real = StorageService._make_visible

    def interrupted(self, staged, target, *, exclusive):
        seen.append((_mode(staged), _mode(staged.parent)))
        raise KeyboardInterrupt

    monkeypatch.setattr(StorageService, "_make_visible", interrupted)
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            with pytest.raises(KeyboardInterrupt):
                daemon.storage.write_capture("hello", scope=PERSONAL, name="a.txt")
            staging = daemon.storage.paths.resolve(PERSONAL, CAPTURE_STAGING)
        finally:
            _close(daemon)
    monkeypatch.setattr(StorageService, "_make_visible", real)

    assert seen == [(0o600, 0o700)]
    assert _mode(staging) == 0o700


def test_config_toml_and_projects_toml_are_private(machine, monkeypatch):
    """Row "config.toml, projects.toml": setup enrols a project and writes the config."""
    with _umask(ORDINARY):
        _setup(machine, monkeypatch)

    assert _mode(machine.application / "config.toml") == 0o600
    assert _mode(machine.application / "projects.toml") == 0o600
    assert _mode(machine.application) == 0o700


def test_a_connector_row_goals_and_the_api_token_are_private(machine):
    """Row "Connector row, goals, API token"."""
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            enrol_connector(
                daemon.storage,
                system="gitlab",
                instance="gitlab:alpha",
                credential="glpat-not-a-real-token",
                probe=lambda system, credential: f"{system} accepted",
            )
            goals = daemon.storage.write_artifact(
                b"# Strategic Goals\n", scope=PERSONAL, artifact=GOALS_ARTIFACT
            )
            daemon_module.take_token(daemon)
            token = daemon.storage.api_token_path()
        finally:
            _close(daemon)

    (row,) = (machine.application / "connectors").iterdir()
    assert _mode(row) == 0o600
    assert _mode(row.parent) == 0o700
    assert _mode(goals) == 0o600
    assert _mode(token) == 0o600


def test_the_disclosure_ledger_and_its_folder_are_private(machine):
    """Row "Disclosure ledger": first record."""
    record = DisclosureRecord(
        at=NOW,
        task_class="summarize",
        model="claude-opus-5",
        contributing_scopes=frozenset({PERSONAL}),
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.01,
    )
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            daemon.storage.append_disclosure(record)
            ledger = daemon.storage.paths.resolve(APPLICATION, "disclosure.md")
        finally:
            _close(daemon)

    assert _mode(ledger) == 0o600
    assert _mode(ledger.parent) == 0o700


def test_lock_files_are_private(machine):
    """Row "Lock files": a dashboard render's `<name>.lock`, and `daemon.lock`."""
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            _config(daemon)
            dashboard = run_dashboard(daemon, scope=PERSONAL, now=NOW)
            daemon.storage.prepare_private_dir()
            held = hold_for_life(daemon.storage.daemon_lock_path())
            held.release()
        finally:
            _close(daemon)

    sidecar = dashboard.with_name(dashboard.name + ".lock")
    assert sidecar.is_file()
    assert _mode(sidecar) == 0o600
    assert _mode(machine.application / "private" / "daemon.lock") == 0o600


def test_the_operational_store_and_sqlites_own_files_are_private(machine):
    """Row "Operational store": `-wal` and `-shm` take the database's mode."""
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            store = machine.application / "private" / "operational.db"
            # Schema preparation has already written through the WAL.
            sidecars = [store.with_name(store.name + suffix) for suffix in ("-wal", "-shm")]
            assert all(path.exists() for path in sidecars), "the store is not in WAL mode"
            modes = {path.name: _mode(path) for path in (store, *sidecars)}
        finally:
            _close(daemon)

    assert modes == {"operational.db": 0o600, "operational.db-wal": 0o600, "operational.db-shm": 0o600}
    assert _mode(store.parent) == 0o700


def test_the_commitment_index_and_its_sqlite_files_are_private(machine):
    """Story 15b: `commitment_index.db`, its `-wal` and `-shm`, with a connection open."""
    import sqlite3

    from commitment_index_fixtures import commitment, ledger, pending
    from pm_ai.core.commitment_index import derive
    from pm_ai.domain.commitments import COMMITMENTS_LOG

    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            ledger_path = daemon.storage.paths.resolve(PERSONAL, COMMITMENTS_LOG, create=True)
            ledger_path.write_bytes(ledger(pending(commitment("prp_one"))))
            derive(daemon.storage, PERSONAL)
            index = machine.application / "private" / "commitment_index.db"
            held = sqlite3.connect(index)
            try:
                held.execute("SELECT COUNT(*) FROM commitments").fetchone()
                sidecars = [index.with_name(index.name + suffix) for suffix in ("-wal", "-shm")]
                assert all(path.exists() for path in sidecars), "the index is not in WAL mode"
                modes = {path.name: _mode(path) for path in (index, *sidecars)}
            finally:
                held.close()
        finally:
            _close(daemon)

    assert modes == {
        "commitment_index.db": 0o600,
        "commitment_index.db-wal": 0o600,
        "commitment_index.db-shm": 0o600,
    }
    assert _mode(index.parent) == 0o700


def test_the_diagnostic_log_is_private_and_stays_so_across_a_rollover(machine):
    """Row "Diagnostic log": rotation stays by rename (4w), every new file 0600."""
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            handler = daemon.storage.diagnostic_log("daemon.log")
            try:
                record = logging.LogRecord("x", logging.INFO, __file__, 1, "one", None, None)
                handler.emit(record)
                assert isinstance(handler, logging.handlers.RotatingFileHandler)
                handler.doRollover()
                handler.emit(record)
            finally:
                handler.close()
        finally:
            _close(daemon)

    logs = machine.application / "logs"
    assert _mode(logs) == 0o700
    assert _mode(logs / "daemon.log") == 0o600
    assert _mode(logs / "daemon.log.1") == 0o600
    assert (logs / "daemon.log.1").read_text(encoding="utf-8").count("\n") == 1


def test_the_personal_event_log_is_private(machine):
    """Row "Personal event log": the umask's answer before this slice."""
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            daemon.storage.append_event_log(_entry("me"), scope=PERSONAL)
            segment = daemon.storage.paths.resolve(PERSONAL, EVENT_LOG) / f"{NOW:%Y-%m}.md"
        finally:
            _close(daemon)

    assert _mode(segment) == 0o600
    assert _mode(segment.parent) == 0o700


def test_an_owner_stripping_umask_cannot_lock_pm_ai_out(machine):
    """Row "Umask strips owner bits": still 0600 and 0700, and writable again."""
    with _umask(OWNER_STRIPPING):
        daemon = machine.daemon(projects={})
        try:
            _config(daemon)
            run_dashboard(daemon, scope=PERSONAL, now=NOW)
            daemon.storage.append_event_log(_entry("one"), scope=PERSONAL)
            daemon.storage.write_capture("hello", scope=PERSONAL, name="a.txt")
            handler = daemon.storage.diagnostic_log("daemon.log")
            handler.close()
        finally:
            _close(daemon)
        # Again, under the same mask: every file is still pm-ai's to write.
        again = machine.daemon(projects={})
        try:
            _config(again, b'display_timezone = "UTC"\nverbose = false\n')
            run_dashboard(again, scope=PERSONAL, now=NOW)
            again.storage.append_event_log(_entry("two"), scope=PERSONAL)
        finally:
            _close(again)

    assert _not_private(machine.application) == []
    assert _not_private(machine.personal) == []


# ── The start-up walk ────────────────────────────────────────────────────────


def _field_test_home(machine: Machine) -> list[Path]:
    """The modes the field test measured, on a home this slice did not make."""
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        _config(daemon)
        daemon.storage.append_event_log(_entry("old"), scope=PERSONAL)
        run_dashboard(daemon, scope=PERSONAL, now=NOW)
        _close(daemon)
    app, personal = machine.application, machine.personal
    (app / "connectors").mkdir(exist_ok=True)
    (app / "logs").mkdir(exist_ok=True)
    (app / "logs" / "daemon.log").write_text("", encoding="utf-8")
    (app / "projects.toml").write_bytes(b"")
    # SQLite removes its side files when the last connection closes; a home
    # from before 1s may still hold them, at the umask's mode.
    for suffix in ("-wal", "-shm"):
        side = app / "private" / f"operational.db{suffix}"
        if not side.exists():
            side.write_bytes(b"")
    wide = [
        app,
        app / "connectors",
        app / "logs",
        app / "logs" / "daemon.log",
        app / "config.toml",
        app / "projects.toml",
        app / "private" / "operational.db",
        personal,
        personal / "memory",
        personal / "memory" / DASHBOARD_ARTIFACT,
    ]
    for path in (*wide, *SIDECARS(app)):
        path.chmod(0o755 if path.is_dir() else 0o644)
    return wide


def SIDECARS(app: Path) -> tuple[Path, Path]:  # noqa: N802 — read as a constant
    """SQLite's side files, wide in `_field_test_home`. Asserted from the walk's
    own report or log: opening the store to walk it, then closing it, lets
    SQLite delete them, so they may be gone afterwards."""
    return (app / "private" / "operational.db-wal", app / "private" / "operational.db-shm")


def test_an_existing_wide_home_is_tightened_with_one_line_each_and_one_console_line(
    machine, caplog
):
    """Row "Existing wide home"."""
    wide = _field_test_home(machine)
    daemon = machine.daemon(projects={})
    try:
        with caplog.at_level(logging.INFO, logger=DAEMON_LOGGER):
            report, lines = _walk(daemon)
    finally:
        _close(daemon)

    for path in wide:
        assert _mode(path) == (0o700 if path.is_dir() else 0o600), path
    changed = {(c.root, c.path) for c in report.tightened}
    assert ("application", ".") in changed
    assert ("application", "connectors") in changed
    assert ("application", "private/operational.db") in changed
    assert ("application", "private/operational.db-wal") in changed
    assert ("application", "private/operational.db-shm") in changed
    assert ("personal", f"memory/{DASHBOARD_ARTIFACT}") in changed
    logged = [r.getMessage() for r in caplog.records if r.name == DAEMON_LOGGER]
    assert "mode tightened: application config.toml 0644 -> 0600" in logged
    assert "mode tightened: application . 0755 -> 0700" in logged
    assert len([line for line in logged if line.startswith("mode tightened: ")]) == len(
        report.tightened
    )
    assert lines == [
        f"pm-ai: made {report.folders} folders and {report.files} files owner-only; "
        f"0 could not be. ~/.pm-ai/logs/daemon.log names each."
    ]


def test_special_bits_and_the_executable_bit_are_kept(machine):
    """Row "Existing special bits": `0755` file → `0700`; `2775` folder → `2700`."""
    skills = machine.personal / "skills"
    skills.mkdir(parents=True)
    script = skills / "tool.py"
    script.write_text("print()\n", encoding="utf-8")
    script.chmod(0o755)
    shared = machine.personal / "shared"
    shared.mkdir()
    shared.chmod(0o2775)
    assert _mode(shared) == 0o2775, "this filesystem did not take the setgid bit"
    daemon = machine.daemon(projects={})
    try:
        _walk(daemon)
    finally:
        _close(daemon)

    assert _mode(script) == 0o700
    assert _mode(shared) == 0o2700


def test_a_home_made_by_this_slice_is_left_as_it_is_and_nothing_is_said(machine, caplog):
    """Row "Already private"."""
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        _config(daemon)
        run_dashboard(daemon, scope=PERSONAL, now=NOW)
        daemon.storage.append_event_log(_entry("me"), scope=PERSONAL)
        _close(daemon)
    daemon = machine.daemon(projects={})
    try:
        with caplog.at_level(logging.INFO, logger=DAEMON_LOGGER):
            report, lines = _walk(daemon)
    finally:
        _close(daemon)

    assert report.tightened == [] and report.refused == []
    assert lines == []
    assert not [r for r in caplog.records if r.name == DAEMON_LOGGER]


def _same_file(target: Path) -> Callable[[int], bool]:
    inode = os.stat(target).st_ino
    return lambda descriptor: os.fstat(descriptor).st_ino == inode


def test_a_path_pm_ai_may_not_change_is_refused_and_the_start_goes_on(machine, caplog):
    """Row "A path pm-ai may not change": another owner, without another account."""
    machine.personal.mkdir()
    foreign = machine.personal / "theirs.md"
    foreign.write_text("x", encoding="utf-8")
    foreign.chmod(0o644)
    mine = machine.personal / "mine.md"
    mine.write_text("x", encoding="utf-8")
    mine.chmod(0o644)
    is_foreign = _same_file(foreign)

    def fchmod(descriptor: int, mode: int) -> None:
        if is_foreign(descriptor):
            raise PermissionError(errno.EPERM, "Operation not permitted")
        os.fchmod(descriptor, mode)

    daemon = machine.daemon(projects={})
    try:
        with caplog.at_level(logging.INFO, logger=DAEMON_LOGGER):
            report, lines = _walk(daemon, calls=ModeCalls(fchmod=fchmod))
    finally:
        _close(daemon)

    assert _mode(foreign) == 0o644
    assert _mode(mine) == 0o600
    (refusal,) = report.refused
    assert (refusal.root, refusal.path) == ("personal", "theirs.md")
    assert "Operation not permitted" in refusal.reason
    assert any(
        r.getMessage().startswith("mode not tightened: personal theirs.md: ")
        for r in caplog.records
    )
    assert len(lines) == 1 and "1 could not be" in lines[0]


def test_a_mode_the_filesystem_does_not_keep_is_not_counted_and_is_reported_again(machine):
    """Row "Mode not kept": the mode read back is the old one, as on exFAT."""
    machine.personal.mkdir()
    machine.personal.chmod(0o700)
    stubborn = machine.personal / "notes.md"
    stubborn.write_text("x", encoding="utf-8")
    stubborn.chmod(0o644)
    is_stubborn = _same_file(stubborn)

    def fchmod(descriptor: int, mode: int) -> None:
        if not is_stubborn(descriptor):
            os.fchmod(descriptor, mode)

    for _start in range(2):
        daemon = machine.daemon(projects={})
        try:
            report, lines = _walk(daemon, calls=ModeCalls(fchmod=fchmod))
        finally:
            _close(daemon)
        assert [c.path for c in report.tightened if c.root == "personal"] == []
        (refusal,) = report.refused
        assert refusal.path == "notes.md"
        assert "did not keep 0600" in refusal.reason and "0644" in refusal.reason
        assert lines == [
            "pm-ai: made 0 folders and 0 files owner-only; 1 could not be. "
            "~/.pm-ai/logs/daemon.log names each."
        ]


def test_an_unreadable_folder_is_logged_and_the_walk_goes_on(machine):
    """Row "Unreadable folder"."""
    machine.personal.mkdir()
    locked = machine.personal / "locked"
    locked.mkdir()
    inside = locked / "inside.md"
    inside.write_text("x", encoding="utf-8")
    inside.chmod(0o644)
    after = machine.personal / "z-after.md"
    after.write_text("x", encoding="utf-8")
    after.chmod(0o644)
    locked.chmod(0o333)
    daemon = machine.daemon(projects={})
    try:
        report, _lines = _walk(daemon)
    finally:
        _close(daemon)
        locked.chmod(0o700)

    assert _mode(inside) == 0o644, "the contents of an unreadable folder were changed"
    assert _mode(after) == 0o600, "the walk stopped at the unreadable folder"
    (refusal,) = report.refused
    assert refusal.path == "locked"
    assert "contents were not walked" in refusal.reason


def test_a_link_inside_a_root_and_its_target_are_untouched(machine, tmp_path):
    """Row "Link inside a root"."""
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x", encoding="utf-8")
    outside.chmod(0o644)
    rules = machine.personal / "rules"
    rules.mkdir(parents=True)
    link = rules / "x.md"
    link.symlink_to(outside)
    before = os.lstat(link).st_mode
    daemon = machine.daemon(projects={})
    try:
        report, _lines = _walk(daemon)
    finally:
        _close(daemon)

    assert _mode(outside) == 0o644
    assert os.lstat(link).st_mode == before
    assert "rules/x.md" not in {c.path for c in report.tightened}


def test_a_root_that_is_a_link_is_followed_once(machine, tmp_path):
    """Row "Root is a link": that folder and its contents are tightened."""
    kept = tmp_path / "kept-elsewhere"
    kept.mkdir()
    kept.chmod(0o755)
    note = kept / "note.md"
    note.write_text("x", encoding="utf-8")
    note.chmod(0o644)
    machine.personal.symlink_to(kept)
    daemon = machine.daemon(projects={})
    try:
        _walk(daemon)
    finally:
        _close(daemon)

    assert _mode(kept) == 0o700
    assert _mode(note) == 0o600


def test_a_root_linked_to_nothing_or_to_a_file_is_logged_and_skipped(machine, tmp_path, caplog):
    """Row "Root link broken or to a file": nothing created."""
    missing = tmp_path / "nowhere"
    machine.personal.symlink_to(missing)
    daemon = machine.daemon(projects={})
    try:
        with caplog.at_level(logging.INFO, logger=DAEMON_LOGGER):
            report, _lines = _walk(daemon)
        assert not missing.exists()
        assert any("link to nothing" in s for s in report.skipped)
        machine.personal.unlink()
        a_file = tmp_path / "a-file"
        a_file.write_text("x", encoding="utf-8")
        a_file.chmod(0o644)
        machine.personal.symlink_to(a_file)
        report, _lines = _walk(daemon)
    finally:
        _close(daemon)

    assert _mode(a_file) == 0o644
    assert any("not a folder" in s for s in report.skipped)
    assert any("mode walk: personal:" in r.getMessage() for r in caplog.records)


def test_a_missing_root_or_project_is_skipped_and_nothing_is_created(machine, tmp_path):
    """Row "Missing root or project": `~/.manager-ai` absent; a project not mounted."""
    unmounted = tmp_path / "Volumes" / "gone" / "alpha"
    daemon = machine.daemon(projects={"alpha": unmounted})
    try:
        report, lines = _walk(daemon)
    finally:
        _close(daemon)

    assert not machine.personal.exists()
    assert not unmounted.exists()
    assert report.refused == [] and report.skipped == []


def test_a_hard_linked_file_is_logged_and_not_changed(machine):
    """Row "Hard-linked file": its other name may be outside the home."""
    machine.personal.mkdir()
    first = machine.personal / "one.md"
    first.write_text("x", encoding="utf-8")
    first.chmod(0o644)
    os.link(first, machine.personal / "two.md")
    daemon = machine.daemon(projects={})
    try:
        report, _lines = _walk(daemon)
    finally:
        _close(daemon)

    assert _mode(first) == 0o644
    assert {r.path for r in report.refused} == {"one.md", "two.md"}
    assert all("hard link" in r.reason for r in report.refused)


def test_a_hard_linked_file_already_owner_only_is_not_reported(machine):
    """Refused only when tightening would change it; otherwise nothing is said."""
    machine.personal.mkdir()
    machine.personal.chmod(0o700)
    first = machine.personal / "one.md"
    first.write_text("x", encoding="utf-8")
    first.chmod(0o600)
    os.link(first, machine.personal / "two.md")
    daemon = machine.daemon(projects={})
    try:
        report, lines = _walk(daemon)
    finally:
        _close(daemon)

    assert [r for r in report.refused if r.root == "personal"] == []
    assert not [c for c in report.tightened if c.root == "personal"]


def test_a_project_inside_a_home_keeps_its_shared_parts_and_its_private_ones_tighten(machine):
    """Row "Project inside a home": a repository under `~/.manager-ai/work/`."""
    repository = machine.personal / "work" / "alpha"
    project = repository / ".project-ai"
    for folder in ("rules", "skills", "memory", "transcripts"):
        (project / folder).mkdir(parents=True)
    (repository / ".gitignore").write_text("x\n", encoding="utf-8")
    (project / "rules" / "persona.md").write_text("x", encoding="utf-8")
    (project / "memory" / "daily_dashboard.md").write_text("x", encoding="utf-8")
    for path in (repository, project, *project.rglob("*")):
        path.chmod(0o755 if path.is_dir() else 0o644)
    (repository / ".gitignore").chmod(0o644)
    daemon = machine.daemon(projects={"alpha": repository})
    try:
        report, _lines = _walk(daemon)
    finally:
        _close(daemon)

    assert _mode(project / "rules") == 0o755
    assert _mode(project / "rules" / "persona.md") == 0o644
    assert _mode(project / "skills") == 0o755
    assert _mode(repository / ".gitignore") == 0o644
    assert _mode(project) == 0o755 and _mode(repository) == 0o755
    assert _mode(project / "memory") == 0o700
    assert _mode(project / "memory" / "daily_dashboard.md") == 0o600
    assert _mode(project / "transcripts") == 0o700
    assert ("alpha", "daily_dashboard.md") in {(c.root, c.path) for c in report.tightened}
    assert _mode(machine.personal / "work") == 0o700


def test_a_path_removed_mid_walk_is_skipped_silently(machine):
    """Row "Path removed mid-walk": a staged file renamed away while the walk runs."""
    temp = machine.personal / "transcripts" / "temp"
    temp.mkdir(parents=True)
    staged = temp / ".a.txt.part"
    staged.write_text("x", encoding="utf-8")
    staged.chmod(0o644)
    published = machine.personal / "transcripts" / "a.txt"

    def lstat(name: str, directory: int) -> os.stat_result:
        found = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if name == staged.name:
            os.rename(staged, published)
        return found

    daemon = machine.daemon(projects={})
    try:
        report, _lines = _walk(daemon, calls=ModeCalls(lstat=lstat))
    finally:
        _close(daemon)

    assert report.refused == []
    assert not staged.exists()


def test_a_file_swapped_for_a_link_mid_walk_changes_nothing_outside(machine, tmp_path):
    """The walk opens each entry `O_NOFOLLOW`, so a swap between list and open is inert."""
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    outside.chmod(0o644)
    machine.personal.mkdir()
    swapped = machine.personal / "swapped.md"
    swapped.write_text("x", encoding="utf-8")
    swapped.chmod(0o644)

    def lstat(name: str, directory: int) -> os.stat_result:
        found = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if name == swapped.name:
            swapped.unlink()
            swapped.symlink_to(outside)
        return found

    daemon = machine.daemon(projects={})
    try:
        report, _lines = _walk(daemon, calls=ModeCalls(lstat=lstat))
    finally:
        _close(daemon)

    assert _mode(outside) == 0o644
    assert report.refused == []


def test_a_projects_shared_parts_are_never_walked(machine):
    """Row "A project's shared parts": `rules/` `0755`, `.gitignore` `0644`, untouched."""
    with _umask(ORDINARY):
        machine.repository.mkdir(parents=True)
        onboard_project(_Keychain(), str(machine.repository))
    project = machine.project
    (project / "rules").chmod(0o755)
    (machine.repository / ".gitignore").chmod(0o644)
    (project / "memory").chmod(0o755)
    daemon = machine.daemon()
    try:
        _walk(daemon)
    finally:
        _close(daemon)

    assert _mode(project / "rules") == 0o755
    assert _mode(machine.repository / ".gitignore") == 0o644
    assert _mode(project) == 0o755
    assert _mode(project / "memory") == 0o700


def test_a_command_without_the_daemon_tightens_nothing_it_did_not_write(machine):
    """Row "Command without the daemon": `pm-ai dashboard` on a wide home."""
    _field_test_home(machine)
    dashboard = machine.personal / "memory" / DASHBOARD_ARTIFACT
    dashboard.unlink()
    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            written = run_dashboard(daemon, scope=PERSONAL, now=NOW)
        finally:
            _close(daemon)

    assert _mode(written) == 0o600
    assert _mode(machine.application / "config.toml") == 0o644
    assert _mode(machine.application) == 0o755
    assert _mode(machine.personal / "memory") == 0o755


def test_the_walk_stops_at_its_budget_says_so_and_the_next_start_finishes(machine, caplog):
    """The walk is bounded (spine gate 2026-10-16); an overrun is logged, not fatal."""
    machine.personal.mkdir()
    for n in range(20):
        path = machine.personal / f"n{n:02}.md"
        path.write_text("x", encoding="utf-8")
        path.chmod(0o644)
    ticks = itertools.count()
    daemon = machine.daemon(projects={})
    try:
        with caplog.at_level(logging.INFO, logger=DAEMON_LOGGER):
            report, lines = _walk(
                daemon, calls=ModeCalls(clock=lambda: float(next(ticks))), budget=5.0
            )
        assert report.overrun is not None
        assert "stopped at its time budget" in lines[0]
        assert any("is walked at the next start" in r.getMessage() for r in caplog.records)
        report, lines = _walk(daemon)
    finally:
        _close(daemon)

    assert report.overrun is None
    assert _not_private(machine.personal) == []


WALK_ENTRIES = 5000
"""How many files the timing row generates (in 50 folders of 100)."""


def test_the_walks_cost_on_a_generated_home(machine):
    """Measured, not assumed: the figure is recorded beside the budget.

    Measured 2026-10-17 on the development Mac: 5,000 files in 50 folders,
    0.32 s to tighten, 0.22 s when already private — the figure
    `MODE_WALK_BUDGET_SECONDS` names. This row fails if a walk of this home
    takes half the budget or more, so a slower machine still passes.
    """
    for folder in range(WALK_ENTRIES // 100):
        directory = machine.personal / "notes" / f"f{folder:02}"
        directory.mkdir(parents=True)
        directory.chmod(0o755)
        for n in range(100):
            path = directory / f"{n:03}.md"
            path.write_text("x", encoding="utf-8")
            path.chmod(0o644)
    daemon = machine.daemon(projects={})
    try:
        started = time.monotonic()
        report, _lines = _walk(daemon, budget=daemon_module.MODE_WALK_BUDGET_SECONDS)
        took = time.monotonic() - started
        assert report.files >= WALK_ENTRIES and report.overrun is None
        again = time.monotonic()
        _walk(daemon)
        quiet = time.monotonic() - again
    finally:
        _close(daemon)

    assert took < daemon_module.MODE_WALK_BUDGET_SECONDS / 2, f"{took:.3f}s"
    assert quiet < daemon_module.MODE_WALK_BUDGET_SECONDS / 2, f"{quiet:.3f}s"


# ── Acceptance ───────────────────────────────────────────────────────────────


def test_a_daemon_started_on_a_tightened_home_renders_and_harvests_privately(machine):
    """Second acceptance criterion: the walk broke nothing pm-ai reads or writes."""
    _field_test_home(machine)
    with _umask(ORDINARY):
        machine.repository.mkdir(parents=True)
        onboard_project(_Keychain(), str(machine.repository))
    daemon = machine.daemon()
    _walk(daemon)
    writer = WriterThread(log=logging.getLogger(DAEMON_LOGGER), step_scope=daemon.storage.claimed_step)
    writer.start()
    pool = IOPool()
    try:
        writer.call(daemon.storage.adopt_daemon_connection, label="adopt")
        pool.start()
        daemon.writer, daemon.pool = writer, pool
        path = RequestPath(
            daemon=daemon, writer=writer, pool=pool, diagnose=lambda: Report(()),
            bounds=DAEMON_BOUNDS,
        )
        commits = _Commits()
        real = RequestPath.fresh
        path.fresh = lambda: replace(  # type: ignore[method-assign]
            real(path), connectors={commits.instance: commits},
            instance_scopes={commits.instance: ALPHA},
        )
        with _umask(ORDINARY):
            renders = RenderTimers(requests=path, now=lambda: NOW)
            asyncio.run(renders.tick())
            HarvestTimers(requests=path, now=lambda: NOW)._attempt(commits.instance)  # noqa: SLF001
        records = {record.scope: record for record in daemon.storage.render_records()}
        assert records[PERSONAL].last_success == NOW
        assert records[ALPHA].last_success == NOW
        assert daemon.storage.load_cursor(commits.instance) == Cursor(b"after")
    finally:
        writer.close()
        pool.shutdown()
        _close(daemon)

    assert _not_private(machine.application) == []
    assert _not_private(machine.personal) == []
    assert _not_private(machine.project / "memory") == []
    assert (machine.project / "memory" / "event_log" / f"{NOW:%Y-%m}.md").is_file()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _console_script() -> list[str]:
    script = Path(sys.executable).with_name("pm-ai")
    return [str(script)] if script.exists() else [sys.executable, "-m", "pm_ai.app.entry"]


def _start_and_stop(home: Path, *, extra_path: Path | None = None) -> list[str]:
    """Start the real daemon, collect stdout up to its listening line, stop it."""
    env = {
        **os.environ,
        **(
            {"PYTHONPATH": os.pathsep.join(
                filter(None, [str(extra_path), os.environ.get("PYTHONPATH", "")])
            )}
            if extra_path is not None
            else {}
        ),
        "HOME": str(home),
        "PM_AI_DISABLE_ENCRYPTION": "1",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
    }
    process = subprocess.Popen(
        [*_console_script(), "daemon", "run"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=home,
    )
    assert process.stdout is not None
    lines: list[str] = []
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], 0.2)
            if ready:
                line = process.stdout.readline()
                if not line:
                    break
                lines.append(line.rstrip("\n"))
                if "listening on" in line:
                    break
            elif process.poll() is not None:
                break
        assert any("listening on" in line for line in lines), (
            lines, process.stderr.read() if process.poll() is not None else ""
        )
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
            process.wait()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    return lines


def test_a_real_daemon_start_tightens_the_field_tests_home_once(machine):
    """First acceptance criterion, through the console script."""
    wide = _field_test_home(machine)
    port = _free_port()
    config = machine.application / "config.toml"
    config.write_text(f'display_timezone = "UTC"\ndaemon_port = {port}\n', encoding="utf-8")
    config.chmod(0o644)

    first = _start_and_stop(machine.home)

    for path in wide:
        assert _mode(path) == (0o700 if path.is_dir() else 0o600), path
    announced = [line for line in first if "owner-only" in line]
    assert len(announced) == 1, first
    assert "daemon.log names each" in announced[0]
    logged = [
        json.loads(line)
        for line in (machine.application / "logs" / "daemon.log").read_text().splitlines()
    ]
    messages = [str(entry.get("message", entry)) for entry in logged]
    assert any("mode tightened: application config.toml 0644 -> 0600" in m for m in messages), messages
    assert any("mode tightened: application . 0755 -> 0700" in m for m in messages), messages
    for side in ("operational.db-wal", "operational.db-shm"):
        assert any(f"application private/{side} 0644 -> 0600" in m for m in messages), side

    second = _start_and_stop(machine.home)
    assert not [line for line in second if "owner-only" in line], second
    assert _not_private(machine.application) == []
    assert _not_private(machine.personal) == []


# ── Review patches (2026-10-17) ──────────────────────────────────────────────


def test_a_repository_registered_at_a_home_root_keeps_its_shared_parts(machine):
    """A registered repository that is the home root itself is fenced too.

    The home's walk then walks only that project's private folders: its
    `rules/`, `skills/` and `.gitignore` are the team's (Ask First).
    """
    repository = machine.personal
    project = repository / ".project-ai"
    for folder in ("rules", "skills", "memory", "transcripts"):
        (project / folder).mkdir(parents=True)
    (repository / ".gitignore").write_text("x\n", encoding="utf-8")
    (project / "rules" / "persona.md").write_text("x", encoding="utf-8")
    (project / "memory" / "daily_dashboard.md").write_text("x", encoding="utf-8")
    loose = repository / "notes.md"
    loose.write_text("x", encoding="utf-8")
    for path in (repository, project, *project.rglob("*"), loose):
        path.chmod(0o755 if path.is_dir() else 0o644)
    (repository / ".gitignore").chmod(0o644)
    daemon = machine.daemon(projects={"alpha": repository})
    try:
        _walk(daemon)
    finally:
        _close(daemon)

    assert _mode(repository) == 0o755
    assert _mode(repository / ".gitignore") == 0o644
    assert _mode(project / "rules") == 0o755
    assert _mode(project / "rules" / "persona.md") == 0o644
    assert _mode(project / "skills") == 0o755
    assert _mode(loose) == 0o644
    assert _mode(project / "memory") == 0o700
    assert _mode(project / "memory" / "daily_dashboard.md") == 0o600
    assert _mode(project / "transcripts") == 0o700


def test_an_unloaded_project_inside_a_home_still_has_its_private_folders_walked(machine):
    """Registered, inside a home, and not loaded: its private folders are still roots."""
    repository = machine.personal / "work" / "beta"
    memory = repository / ".project-ai" / "memory"
    memory.mkdir(parents=True)
    (repository / ".project-ai" / "rules").mkdir()
    (repository / ".project-ai" / "rules").chmod(0o755)
    memory.chmod(0o755)
    page = memory / "daily_dashboard.md"
    page.write_text("x", encoding="utf-8")
    page.chmod(0o644)
    outside = machine.root / "work" / "gamma" / ".project-ai" / "memory"
    outside.mkdir(parents=True)
    outside.chmod(0o755)
    daemon = build(
        None,
        PERSONAL,
        watched=(),
        paths=ScopePaths.production(
            home=machine.home,
            projects={"beta": repository, "gamma": outside.parent.parent},
        ),
        keychain=_Keychain(),
        encryption_disabled=True,
        now=lambda: NOW,
        vcs=_Vcs(repository),
        config=Config(display_timezone="UTC"),
    )
    try:
        report, _lines = _walk(daemon)
    finally:
        _close(daemon)

    assert _mode(memory) == 0o700
    assert _mode(page) == 0o600
    assert ("beta", "daily_dashboard.md") in {(c.root, c.path) for c in report.tightened}
    assert _mode(repository / ".project-ai" / "rules") == 0o755
    assert _mode(outside) == 0o755, "a project outside the homes was walked unloaded"


def test_a_wide_token_is_tightened_by_a_real_start_before_it_is_read(machine):
    """T1: the walk runs before `take_token`, through `_serve` in the console script."""
    port = _free_port()
    machine.application.mkdir()
    config = machine.application / "config.toml"
    config.write_text(f'display_timezone = "UTC"\ndaemon_port = {port}\n', encoding="utf-8")
    token = machine.application / "private" / "api_token"
    token.parent.mkdir()
    token.write_text("t" * 43, encoding="utf-8")
    token.chmod(0o640)

    lines = _start_and_stop(machine.home)

    assert any("listening on" in line for line in lines), lines
    assert _mode(token) == 0o600
    assert token.read_text(encoding="utf-8") == "t" * 43


def test_onboarding_through_the_daemon_makes_private_folders_and_a_job_may_not(machine):
    """T2: `onboard_in_daemon` under umask 022; `prepare_directory` refused to a job."""
    from pm_ai.app.wiring import HandedStorage, onboard_in_daemon

    with _umask(ORDINARY):
        daemon = machine.daemon(projects={})
        try:
            machine.repository.mkdir(parents=True)
            onboard_in_daemon(
                daemon, str(machine.repository), None, hand=lambda work, label: work()
            )
            handed = HandedStorage(daemon.storage, hand=lambda work, label: work())
            with pytest.raises(AttributeError, match="prepare_directory"):
                handed.prepare_directory
        finally:
            _close(daemon)

    project = machine.project
    assert _mode(project / "memory") == 0o700
    assert _mode(project / "transcripts") == 0o700
    assert _mode(project) == 0o755
    assert _mode(project / "rules") == 0o755
    assert _mode(project / "skills") == 0o755


def test_the_lifetime_lock_is_private_under_an_owner_stripping_umask(machine):
    """T3: `take_lifetime_lock` on a fresh `private/` at umask 200."""
    daemon = machine.daemon(projects={})
    lock = daemon.storage.daemon_lock_path()
    private = lock.parent
    assert not lock.exists()
    try:
        with _umask(OWNER_STRIPPING):
            held = daemon_module.take_lifetime_lock(daemon)
        held.release()
    finally:
        _close(daemon)

    assert _mode(lock) == 0o600
    assert _mode(private) == 0o700


def test_a_root_whose_walk_crashes_does_not_stop_the_others(machine, caplog, monkeypatch):
    """T4: a non-`OSError` from one root is logged with that root; the rest walk."""
    machine.personal.mkdir()
    note = machine.personal / "note.md"
    note.write_text("x", encoding="utf-8")
    note.chmod(0o644)
    daemon = machine.daemon(projects={})
    real = daemon.storage.tighten_modes

    def crashing(root, *, label, **kwargs):
        if label == "application":
            raise RuntimeError("a bug in the walk")
        return real(root, label=label, **kwargs)

    monkeypatch.setattr(daemon.storage, "tighten_modes", crashing)
    try:
        with caplog.at_level(logging.INFO, logger=DAEMON_LOGGER):
            report, _lines = _walk(daemon)
    finally:
        _close(daemon)

    assert _mode(note) == 0o600
    assert ("personal", "note.md") in {(c.root, c.path) for c in report.tightened}
    (stopped,) = [r for r in caplog.records if "stopped" in r.getMessage()]
    assert "application" in stopped.getMessage()
    assert stopped.exc_info is not None


def test_a_start_still_serves_when_the_walk_crashes(machine, tmp_path):
    """T4, through the console script: a crashing walk never stops the start."""
    port = _free_port()
    machine.application.mkdir()
    (machine.application / "config.toml").write_text(
        f'display_timezone = "UTC"\ndaemon_port = {port}\n', encoding="utf-8"
    )
    # A `sitecustomize` on the child's path makes every root's walk raise.
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(
        "from pm_ai.storage import service\n"
        "def _crash(self, root, **kwargs):\n"
        "    raise RuntimeError('a bug in the walk')\n"
        "service.StorageService.tighten_modes = _crash\n",
        encoding="utf-8",
    )
    lines = _start_and_stop(machine.home, extra_path=hooks)

    assert any("listening on" in line for line in lines), lines
    logged = (machine.application / "logs" / "daemon.log").read_text(encoding="utf-8")
    assert "the start-up mode walk of application" in logged


def test_every_private_project_artifact_lies_under_memory_or_transcripts():
    """T6: the file rule (`GITIGNORED`) and the folder rule cannot drift apart."""
    from pm_ai.domain.scope_model import GITIGNORED
    from pm_ai.domain.storage_tiers import project_private_dirs

    folders = project_private_dirs()
    assert folders == ("memory/", "transcripts/")
    outside = sorted(
        key for key in GITIGNORED[ScopeKind.PROJECT]
        if not any(key == folder or key.startswith(folder) for folder in folders)
    )
    assert outside == [], (
        "a project artifact is declared machine-local outside memory/ and "
        f"transcripts/, so its file would be 0600 in a folder left at the umask: {outside}"
    )
