"""Story 15b — the commitment index.

One test per row of the story's I/O matrix that the slice reaches (the
rows-are-the-fold cases are `test_ad35_commitment_index_rows_are_the_fold`,
the rebuild's `test_ad3_…`, both in `tests/architecture`; the pinned values,
the snapshot order, the subset rebuild and the conditional replace are in
`tests/core/test_commitment_index_rows.py`), plus the timed tests and the
50,000-line measurement.

Every row runs against a temporary home with `alpha` enrolled at a real git
repository (13a's `Machine`) and the daemon's writer thread and I/O pool
(15a's `_Rig`); the clock is the test's and no test reaches a model. Names
are `11d`'s fixture names; handles are `example.com`. Nothing reads the PM's
own store.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import types
from datetime import timezone
from pathlib import Path

import pytest

from commitment_index_fixtures import after, commitment, ledger, pending, verdict
from test_commitment_commands import _approved, _desk, _fill
from test_commitments import ITEMS, _Rig, _approve_in_store, _ledger_path, _summarised
from test_proposals import ALPHA, APPLICATION, PERSONAL, Machine
from pm_ai.app import commitment_indexing as indexing_module
from pm_ai.app import daemon as daemon_module
from pm_ai.app.commitment_indexing import CommitmentIndexing
from pm_ai.core.commitment_index import derive, index_rows
from pm_ai.core.commitment_log import FOLD_VERSION, fold, parse_ledger
from pm_ai.core.jobs import source_md5
from pm_ai.domain.commitments import COMMITMENTS_LOG, NotACommitmentScope
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import LedgerChanged
from pm_ai.storage import commitment_index_store as index_store
from pm_ai.storage.service import StorageService
from pm_ai.surfaces.cli.dispatch import EXIT_OK

LOGGER = "pm_ai.daemon"


@pytest.fixture
def machine(tmp_path, monkeypatch) -> Machine:
    return Machine(tmp_path, monkeypatch)


# ── A serving daemon ─────────────────────────────────────────────────────────


class Serving:
    """`attach_background` and `_lifespan`, as the serve path runs them, on a loop of its own."""

    def __init__(self, machine: Machine, *, catch_up: bool = True, before=None) -> None:
        self.machine = machine
        self.rig = _Rig(machine)
        state = types.SimpleNamespace()
        daemon_module.attach_background(state, self.rig.path, now=lambda: machine.clock)
        self.indexing: CommitmentIndexing = state.pm_ai_index
        if before is not None:
            before(self)
        attached = {"pm_ai_requests": self.rig.path, "pm_ai_index": self.indexing}
        if catch_up:
            attached["pm_ai_commitments"] = state.pm_ai_commitments
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(**attached))
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="test-loop", daemon=True)
        self.thread.start()
        self._stop: asyncio.Event | None = None
        self._entered = threading.Event()
        self._served = asyncio.run_coroutine_threadsafe(self._serve(), self.loop)
        assert self._entered.wait(10)

    @property
    def storage(self) -> StorageService:
        return self.rig.daemon.storage

    async def _serve(self) -> None:
        self._stop = asyncio.Event()
        async with daemon_module._lifespan(self.app):  # noqa: SLF001
            self._entered.set()
            await self._stop.wait()

    def on_loop(self, work):
        async def run():
            return work()

        return asyncio.run_coroutine_threadsafe(run(), self.loop).result(10)

    def prompt(self, line: str):
        reply = asyncio.run_coroutine_threadsafe(self.rig.path.prompt(line), self.loop).result(30)
        assert reply.body["exit"] == EXIT_OK, reply.body
        return reply

    def settle(self, timeout: float = 10.0) -> None:
        """Until the start check has run and every request is derived."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.on_loop(lambda: self.indexing.idle):
                # Once more, so a request posted by the last append is seen.
                time.sleep(0.02)
                if self.on_loop(lambda: self.indexing.idle):
                    return
            time.sleep(0.01)
        raise AssertionError("the indexer did not settle")

    def close(self) -> None:
        if self._stop is not None:
            self.loop.call_soon_threadsafe(self._stop.set)
        self._served.result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
        self.loop.close()
        self.rig.close()


def _serving(machine: Machine, **options) -> Serving:
    return Serving(machine, **options)


def _md5(machine: Machine, scope: DataScope = PERSONAL) -> str:
    path = _ledger_path(machine, scope)
    return source_md5(path.read_bytes() if path.exists() else b"")


def _index_path(machine: Machine) -> Path:
    return Path(machine.storage.paths.resolve(APPLICATION, index_store.COMMITMENT_INDEX))


def _rows(machine: Machine, scope: DataScope | None = None):
    return machine.storage.commitment_index_rows(None if scope is None else str(scope))


def _row(machine: Machine, root: str, scope: DataScope = PERSONAL):
    (found,) = [row for row in _rows(machine, scope) if row.commitment_id == root]
    return found


def _fold(machine: Machine, scope: DataScope = PERSONAL):
    return fold(parse_ledger(_ledger_path(machine, scope).read_bytes()))


def _expected(machine: Machine, scope: DataScope = PERSONAL):
    data = _ledger_path(machine, scope).read_bytes()
    return index_rows(str(scope), fold(parse_ledger(data)), source_md5(data))


def _write(machine: Machine, data: bytes, scope: DataScope = PERSONAL) -> None:
    path = _ledger_path(machine, scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _start_line(caplog) -> str:
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("commitment index at start")]
    return line


def _messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def _derived_at_start(machine: Machine, *scopes: DataScope) -> None:
    """Every scope given derived once, as a start would, so the index holds them current."""
    for scope in scopes:
        derive(machine.storage, scope)


# ── Through the daemon ───────────────────────────────────────────────────────


def test_an_approval_at_the_prompt_writes_its_row(machine):
    """Matrix "Approval through the daemon"; acceptance 1. The wiring slice test:
    the state `attach_background` filled is the one `_lifespan` runs."""
    _, ids = _summarised(machine)
    serving = _serving(machine)
    try:
        serving.settle()
        serving.prompt(f"proposal approve {ids[0]}")
        serving.settle()
    finally:
        serving.close()
    (row,) = _rows(machine)
    assert (row.commitment_id, row.state, row.state_source) == (ids[0], "pending", "none")
    assert row.version == _fold(machine).details[ids[0]].version
    assert row.source_md5 == _md5(machine)


def test_a_mark_at_the_prompt_moves_its_row_and_every_rows_checksum(machine):
    """Matrix "Mark through the daemon"."""
    _, ids = _summarised(machine)
    serving = _serving(machine)
    try:
        serving.settle()
        for each in ids:
            serving.prompt(f"proposal approve {each}")
        serving.settle()
        serving.prompt(f"commitment done {ids[0]}")
        serving.settle()
    finally:
        serving.close()
    held = _fold(machine).details[ids[0]]
    row = _row(machine, ids[0])
    assert (row.state, row.state_source) == ("fulfilled", "pm")
    assert row.state_at == held.state_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    assert row.state_at.endswith("+00:00")
    assert row.version == held.version
    assert {each.source_md5 for each in _rows(machine)} == {_md5(machine)}
    assert _rows(machine) == _expected(machine)


def test_a_due_set_at_the_prompt(machine):
    """Matrix "Due set": the PM's date, not estimated."""
    _, ids = _summarised(machine)
    serving = _serving(machine)
    try:
        serving.prompt(f"proposal approve {ids[0]}")
        serving.prompt(f"commitment due {ids[0]} friday")
        serving.settle()
    finally:
        serving.close()
    row = _row(machine, ids[0])
    assert (row.due_date, row.due_value, row.due_by_pm, row.due_estimated) == ("2026-10-23", "2026-10-23", 1, 0)


def test_an_owner_set_at_the_prompt_changes_the_version_and_no_column_holds_the_name(machine):
    """Matrix "Owner set"."""
    _, ids = _summarised(machine)
    serving = _serving(machine)
    try:
        serving.prompt(f"proposal approve {ids[0]}")
        serving.settle()
        before = _row(machine, ids[0])
        serving.prompt(f'commitment owner {ids[0]} "Alex Morgan"')
        serving.settle()
    finally:
        serving.close()
    row = _row(machine, ids[0])
    assert row.version != before.version and row.source_md5 != before.source_md5
    values = [str(value) for value in (row.__getattribute__(name) for name in index_store.INDEX_COLUMNS)]
    assert not any("Alex" in value or "Sam" in value or "Rivera" in value for value in values)
    assert not any("cache" in value for value in values), "no commitment words in a row"


def test_altered_with_wording_requests_the_scope_for_each_append(machine):
    """Matrix "Altered with wording"."""
    _, ids = _summarised(machine)
    asked: list[str] = []

    def counting(serving: Serving) -> None:
        real = serving.indexing.hook

        def hook(scope):
            asked.append(str(scope))
            real(scope)

        serving.storage.on_commitments_appended(hook)

    serving = _serving(machine, before=counting)
    try:
        serving.prompt(f"proposal approve {ids[0]}")
        serving.settle()
        asked.clear()
        serving.prompt(f'commitment altered {ids[0]} --what "send the numbers for the API only"')
        serving.settle()
    finally:
        serving.close()
    assert asked == ["personal", "personal"], "the mark's append and the revised line's"
    row = _row(machine, ids[0])
    assert (row.proposal_id, row.state) == (f"{ids[0]}_e1", "altered")


def test_an_undo_at_the_prompt_returns_the_row_to_its_state_before(machine):
    """Matrix "Undo"."""
    _, ids = _summarised(machine)
    serving = _serving(machine)
    try:
        serving.prompt(f"proposal approve {ids[0]}")
        serving.prompt(f"commitment done {ids[0]}")
        serving.prompt(f"commitment undo {ids[0]}")
        serving.settle()
    finally:
        serving.close()
    row = _row(machine, ids[0])
    held = _fold(machine).details[ids[0]]
    assert (row.state, row.state_source) == ("pending", "none")
    assert held.newest.kind.value == "undone" and row.version == held.version


def test_a_hand_written_verdict_is_indexed_at_the_next_start(machine):
    """Matrix "Telemetry verdict"."""
    ids = _approved(machine)
    with open(_ledger_path(machine), "a", encoding="utf-8") as file:
        file.write(verdict(ids[0], "fulfilled", machine.clock) + "\n")
    serving = _serving(machine)
    try:
        serving.settle()
    finally:
        serving.close()
    row = _row(machine, ids[0])
    assert (row.state, row.state_source) == ("fulfilled", "telemetry")


def test_the_catch_up_at_start_is_indexed_and_then_current(machine):
    """Matrix "Catch-up at start"."""
    _, ids = _summarised(machine)
    for each in ids:
        _approve_in_store(machine, each)
    serving = _serving(machine)
    try:
        serving.settle()
        done = list(serving.indexing.done)
    finally:
        serving.close()
    assert {row.commitment_id for row in _rows(machine)} == set(ids)
    assert _rows(machine) == _expected(machine)
    starts = [each for each in done if each.scope == "personal"]
    assert starts[0].outcome == "derived" and all(each.outcome == "current" for each in starts[1:])


def test_two_quick_marks_end_as_the_fold_of_the_file_after_both(machine, monkeypatch):
    """Matrix "Two quick appends": at most two derivations, never overlapping."""
    ids = _approved(machine)
    running = []
    overlapped = []
    real = indexing_module.derive

    def watched(*args, **kwargs):
        if running:
            overlapped.append(True)
        running.append(True)
        try:
            return real(*args, **kwargs)
        finally:
            running.pop()

    monkeypatch.setattr(indexing_module, "derive", watched)
    serving = _serving(machine)
    try:
        serving.settle()
        before = len(serving.indexing.done)
        serving.prompt(f"commitment done {ids[0]}")
        serving.prompt(f"commitment done {ids[1]}")
        serving.settle()
        after_marks = serving.indexing.done[before:]
    finally:
        serving.close()
    assert not overlapped and 1 <= len(after_marks) <= 2
    assert _rows(machine) == _expected(machine)


def test_a_hot_scope_does_not_starve_another(machine, monkeypatch):
    """Matrix "Hot scope": first in, first out, and a scope asked for again during
    its own derivation is taken after the scopes ahead of it."""
    order: list[str] = []
    serving = _serving(machine)
    try:
        serving.settle()
        real = indexing_module.derive

        def recorded(storage, scope, **kwargs):
            order.append(str(scope))
            if order.count("personal") <= 2 and scope == PERSONAL:
                # Personal is requested again, and again, while it is being derived.
                serving.loop.call_soon_threadsafe(serving.indexing.request, PERSONAL)
            return real(storage, scope, **kwargs)

        monkeypatch.setattr(indexing_module, "derive", recorded)

        def burst():
            for scope in (PERSONAL, ALPHA, PERSONAL, PERSONAL):
                serving.indexing.request(scope)

        serving.on_loop(burst)
        serving.settle()
    finally:
        serving.close()
    assert order[:3] == ["personal", "project:alpha", "personal"]
    assert order.count("project:alpha") == 1


def test_an_append_that_raised_after_writing_is_derived(machine, monkeypatch):
    """Matrix "Append raised after writing": the hook fired."""
    _, ids = _summarised(machine)
    serving = _serving(machine)
    try:
        serving.settle()
        real = StorageService._append

        def wrote_then_failed(self, target, text, **kwargs):
            real(self, target, text, **kwargs)
            raise OSError("the disk said no after the write")

        monkeypatch.setattr(StorageService, "_append", wrote_then_failed)
        reply = asyncio.run_coroutine_threadsafe(
            serving.rig.path.prompt(f"proposal approve {ids[0]}"), serving.loop
        ).result(30)
        assert reply.body["exit"] == EXIT_OK
        monkeypatch.setattr(StorageService, "_append", real)
        serving.settle()
    finally:
        serving.close()
    assert [row.commitment_id for row in _rows(machine)] == [ids[0]]
    assert _rows(machine)[0].source_md5 == _md5(machine)


def test_an_append_refused_at_the_size_check_fires_nothing(machine):
    """Matrix "Append refused early"."""
    fired: list[str] = []
    machine.storage.on_commitments_appended(lambda scope: fired.append(str(scope)))
    with pytest.raises(LedgerChanged):
        machine.storage.append_commitments([pending(commitment("prp_one"))], scope=PERSONAL, after=7)
    assert fired == []
    machine.storage.append_commitments([pending(commitment("prp_one"))], scope=PERSONAL, after=0)
    assert fired == ["personal"]


def test_a_terminal_append_is_indexed_at_the_daemons_next_append_or_start(machine):
    """Matrix "Terminal append" (Andrei, 2026-10-27: the daemon only)."""
    ids = _approved(machine)
    serving = _serving(machine)
    try:
        serving.settle()
        old = _row(machine, ids[0]).source_md5
        # At a terminal: its own store, which has no hook.
        assert _desk(machine).mark("done", ids[0]).outcome == "marked"
        time.sleep(0.2)
        assert serving.on_loop(lambda: serving.indexing.idle)
        assert _row(machine, ids[0]).state == "pending" and _row(machine, ids[0]).source_md5 == old
        serving.prompt(f"commitment done {ids[1]}")
        serving.settle()
    finally:
        serving.close()
    assert _row(machine, ids[0]).state == "fulfilled"
    assert _rows(machine) == _expected(machine)
    # And a terminal append with the daemon down is derived at its next start.
    assert _desk(machine).mark("broken", ids[2], note="moved to next sprint").outcome == "marked"
    serving = _serving(machine)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _row(machine, ids[2]).state == "broken"


def test_a_hand_edit_while_stopped_derives_that_scope_alone(machine, caplog):
    """Matrix "Hand edit, daemon down"; acceptance 2."""
    ids = _approved(machine)
    _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    _derived_at_start(machine, PERSONAL, ALPHA)
    alpha_before = _rows(machine, ALPHA)
    with open(_ledger_path(machine), "a", encoding="utf-8") as file:
        file.write("# note\n")
    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine)
    try:
        serving.settle()
    finally:
        serving.close()
    assert {row.source_md5 for row in _rows(machine, PERSONAL)} == {_md5(machine)}
    assert _rows(machine, ALPHA) == alpha_before
    assert "commitment index at start: 1 current, 1 derived again, 0 scopes removed, 0 unreadable" in _start_line(caplog)
    assert ids


def test_a_start_with_nothing_changed_writes_nothing(machine, caplog):
    """Matrix "Nothing changed": personal with no ledger is current too."""
    _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    _derived_at_start(machine, ALPHA)
    stamp = _index_path(machine).stat().st_mtime_ns
    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _index_path(machine).stat().st_mtime_ns == stamp
    assert _start_line(caplog).startswith("commitment index at start: 2 current, 0 derived again")
    assert not _ledger_path(machine).exists()


def test_a_ledger_deleted_while_stopped_has_its_rows_deleted(machine):
    """Matrix "Ledger deleted"."""
    _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    _derived_at_start(machine, ALPHA)
    _ledger_path(machine, ALPHA).unlink()
    serving = _serving(machine)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _rows(machine, ALPHA) == ()


def test_a_missing_project_root_is_unreadable_and_keeps_its_rows(machine, caplog):
    """Matrix "Project root missing"."""
    _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    _derived_at_start(machine, ALPHA)
    kept = _rows(machine, ALPHA)
    shutil.rmtree(machine.projects["alpha"])
    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _rows(machine, ALPHA) == kept
    assert "commitment index: project:alpha not derived (ProjectRootMissing); the next append or start tries again" in _messages(caplog)
    assert ", 1 unreadable," in _start_line(caplog)


def _drop_alpha_from_the_registry(machine: Machine) -> None:
    registry = Path(machine.storage.paths.resolve(APPLICATION, "projects.toml"))
    registry.write_text("")


def test_a_project_dropped_from_the_registry_has_its_rows_removed(machine, caplog):
    """Matrix "Project dropped"."""
    _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    _derived_at_start(machine, ALPHA)
    _drop_alpha_from_the_registry(machine)
    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _rows(machine, ALPHA) == ()
    assert ", 1 scopes removed," in _start_line(caplog)


def test_a_removal_that_fails_keeps_the_rows_and_the_start_line(machine, monkeypatch, caplog):
    """Matrix "Project dropped", when the removal step meets a busy index."""
    _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    _derived_at_start(machine, ALPHA)
    kept = _rows(machine, ALPHA)
    _drop_alpha_from_the_registry(machine)

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(index_store, "remove_scopes", busy)
    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _rows(machine, ALPHA) == kept
    assert ", 0 scopes removed," in _start_line(caplog)
    assert (
        "commitment index: rows of dropped projects not removed (OperationalError); the next start tries again"
        in _messages(caplog)
    )


def test_an_unreadable_registry_removes_no_rows(machine, caplog):
    """Matrix "Registry unreadable"."""
    _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    _derived_at_start(machine, ALPHA)
    kept = _rows(machine, ALPHA)
    registry = Path(machine.storage.paths.resolve(APPLICATION, "projects.toml"))
    registry.write_text("[[[ not toml\n")
    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _rows(machine, ALPHA) == kept
    assert "scopes not removed: the projects registry could not be read" in _messages(caplog)


def test_no_commitment_anywhere_creates_no_index_file(machine):
    """Matrix "Empty or absent ledger"."""
    _write(machine, b"# a heading the PM wrote\n", ALPHA)
    serving = _serving(machine)
    try:
        serving.settle()
        assert serving.indexing.last_check is not None
        assert serving.indexing.last_check.count("current") == 2
    finally:
        serving.close()
    assert not _index_path(machine).exists()


def test_a_torn_last_line_is_derived_once_it_completes(machine):
    """Matrix "Torn last line": the checksum covers its bytes; completed, the next check derives it."""
    whole = pending(commitment("prp_one"))
    torn = pending(commitment("prp_two"))
    _write(machine, ledger(whole) + torn.encode())
    _derived_at_start(machine, PERSONAL)
    assert [row.commitment_id for row in _rows(machine)] == ["prp_one"]
    assert _rows(machine)[0].source_md5 == _md5(machine)
    with open(_ledger_path(machine), "a", encoding="utf-8") as file:
        file.write("\n")
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
    finally:
        serving.close()
    assert [row.commitment_id for row in _rows(machine)] == ["prp_one", "prp_two"]


def test_an_unreadable_ledger_keeps_its_rows_and_warns_once(machine, caplog):
    """Matrix "Ledger unreadable"."""
    _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    _derived_at_start(machine, ALPHA)
    kept = _rows(machine, ALPHA)
    path = _ledger_path(machine, ALPHA)
    path.write_bytes(path.read_bytes() + ledger(pending(commitment("prp_alpha_two"))))
    os.chmod(path, 0)
    caplog.set_level(logging.INFO, logger=LOGGER)
    try:
        serving = _serving(machine, catch_up=False)
        try:
            serving.settle()
        finally:
            serving.close()
    finally:
        os.chmod(path, 0o600)
    assert _rows(machine, ALPHA) == kept
    failures = [m for m in _messages(caplog) if m.startswith("commitment index: project:alpha not derived")]
    assert failures == [
        "commitment index: project:alpha not derived (PermissionError); the next append or start tries again"
    ]


def test_a_derivation_that_raises_does_not_stop_the_indexer(machine, monkeypatch, caplog):
    """Matrix "Derivation raises"."""
    _write(machine, ledger(pending(commitment("prp_one"))))
    _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    real = indexing_module.derive

    def failing(storage, scope, **kwargs):
        if scope == PERSONAL:
            raise RuntimeError("an unexpected failure with words that are never logged")
        return real(storage, scope, **kwargs)

    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
        monkeypatch.setattr(indexing_module, "derive", failing)
        serving.on_loop(lambda: [serving.indexing.request(PERSONAL), serving.indexing.request(ALPHA)])
        serving.settle()
        _write(machine, ledger(pending(commitment("prp_alpha_one")), pending(commitment("prp_alpha_two"))), ALPHA)
        serving.on_loop(lambda: serving.indexing.request(ALPHA))
        serving.settle()
    finally:
        serving.close()
    assert "commitment index: personal not derived (RuntimeError); the next append or start tries again" in _messages(caplog)
    assert not any("never logged" in message for message in _messages(caplog))
    assert [row.commitment_id for row in _rows(machine, ALPHA)] == ["prp_alpha_one", "prp_alpha_two"]


# ── The index file ───────────────────────────────────────────────────────────


def _started_with(machine: Machine, caplog) -> Serving:
    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
    finally:
        serving.close()
    return serving


@pytest.mark.parametrize(
    ("spoil", "reason"),
    [
        (lambda path: path.write_bytes(b"these bytes are not a database at all" * 200), "not_a_database"),
        (lambda path: _sql(path, "DROP TABLE index_format"), "partial"),
        (lambda path: _sql(path, "DELETE FROM index_format"), "format_row"),
        (lambda path: _sql(path, "UPDATE index_format SET index_format = 2"), "other_format"),
        (lambda path: _sql(path, f"UPDATE index_format SET fold_version = {FOLD_VERSION + 1}"), "other_format"),
        (lambda path: _sql(path, "UPDATE index_format SET index_format = 'one'"), "format_row"),
        (lambda path: _sql(path, "ALTER TABLE commitments ADD COLUMN owner TEXT"), "other_format"),
    ],
    ids=[
        "not-a-database", "partly-made", "format-row-empty", "index-format-2", "other-fold-version",
        "not-an-integer", "other-columns",
    ],
)
@pytest.mark.parametrize("when", ["at-start", "while-serving"])
def test_an_unusable_index_is_dropped_and_rebuilt(machine, caplog, spoil, reason, when):
    """Matrix "Index not a database", "Index partly made", "Another format": found by
    the start check, or by a derivation after it."""
    _write(machine, ledger(pending(commitment("prp_one"))))
    _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
    _derived_at_start(machine, PERSONAL, ALPHA)

    def spoiled() -> None:
        for suffix in ("-wal", "-shm"):
            Path(f"{_index_path(machine)}{suffix}").unlink(missing_ok=True)
        spoil(_index_path(machine))

    if when == "at-start":
        spoiled()
        _started_with(machine, caplog)
    else:
        caplog.set_level(logging.INFO, logger=LOGGER)
        serving = _serving(machine, catch_up=False)
        try:
            serving.settle()
            spoiled()
            _write(machine, ledger(pending(commitment("prp_one")), pending(commitment("prp_two"))))
            serving.on_loop(lambda: serving.indexing.request(PERSONAL))
            serving.settle()
        finally:
            serving.close()
    assert f"the index was unusable ({reason}); dropped and rebuilt" in _messages(caplog)
    assert _rows(machine, PERSONAL) == _expected(machine, PERSONAL)
    assert _rows(machine, ALPHA) == _expected(machine, ALPHA)


def _sql(path: Path, statement: str) -> None:
    db = sqlite3.connect(path)
    try:
        db.execute(statement)
        db.commit()
    finally:
        db.close()


def test_an_index_with_no_tables_gets_its_schema_and_is_not_dropped(machine, caplog):
    """Matrix "Index with no tables"."""
    _write(machine, ledger(pending(commitment("prp_one"))))
    path = _index_path(machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    inode = path.stat().st_ino
    _started_with(machine, caplog)
    assert not any("unusable" in message for message in _messages(caplog))
    assert path.stat().st_ino == inode
    assert _rows(machine) == _expected(machine)


def test_a_failed_drop_latches_the_index_until_the_next_start(machine, monkeypatch, caplog):
    """Matrix "Drop or rebuild fails": one log line, later derivations skipped."""
    _write(machine, ledger(pending(commitment("prp_one"))))
    _index_path(machine).parent.mkdir(parents=True, exist_ok=True)
    _index_path(machine).write_bytes(b"not a database" * 100)

    def refused(self):
        raise OSError("cannot remove")

    real = StorageService.drop_commitment_index
    monkeypatch.setattr(StorageService, "drop_commitment_index", refused)
    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
        serving.prompt("commitment list")
        for _ in range(3):
            serving.on_loop(lambda: serving.indexing.request(PERSONAL))
            serving.settle()
        done = len(serving.indexing.done)
    finally:
        serving.close()
    latch = [m for m in _messages(caplog) if m.startswith("commitment index unusable and not rebuilt")]
    assert latch == ["commitment index unusable and not rebuilt (OSError); derivations skipped until the next start"]
    assert done == 0
    # Only the drop is put back: `monkeypatch.undo()` would also undo the
    # temporary HOME and the real-home guard.
    monkeypatch.setattr(StorageService, "drop_commitment_index", real)
    _started_with(machine, caplog)
    assert _rows(machine) == _expected(machine)


def test_a_busy_replace_rolls_back_keeps_the_rows_and_drops_nothing(machine, monkeypatch, caplog):
    """Matrix "Busy or full"."""
    _write(machine, ledger(pending(commitment("prp_one"))))
    _derived_at_start(machine, PERSONAL)
    kept = _rows(machine)
    _write(machine, ledger(pending(commitment("prp_one")), pending(commitment("prp_two"))))
    real = index_store.replace_rows

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(index_store, "replace_rows", busy)
    _started_with(machine, caplog)
    assert _rows(machine) == kept
    assert "commitment index: personal not derived (OperationalError); the next append or start tries again" in _messages(caplog)
    assert not any("unusable" in message for message in _messages(caplog))
    assert real


def test_a_scope_changed_meanwhile_is_derived_again(machine, monkeypatch, caplog):
    """Matrix "Changed meanwhile": nothing written; the scope stays requested."""
    _write(machine, ledger(pending(commitment("prp_one"))))
    _derived_at_start(machine, PERSONAL)
    _write(machine, ledger(pending(commitment("prp_one")), pending(commitment("prp_two"))))
    real = StorageService.replace_commitment_rows
    raced: list[bool] = []

    def another_writer_first(self, scope, rows, expected_md5s):
        if not raced:
            raced.append(True)
            # Another writer — a second store — derives the scope between this
            # derivation's read and its replace; then the ledger grows again, so
            # only a derivation after this one makes the rows current.
            derive(machine.storage, scope, force=True)
            _write(machine, ledger(*(pending(commitment(f"prp_{n}")) for n in ("one", "two", "three"))))
        return real(self, scope, rows, expected_md5s)

    monkeypatch.setattr(StorageService, "replace_commitment_rows", another_writer_first)
    serving = _started_with(machine, caplog)
    assert "commitment index: personal changed meanwhile; derived again" in _messages(caplog)
    assert [each.outcome for each in serving.indexing.done if each.scope == "personal"] == ["changed", "derived"]
    assert [row.commitment_id for row in _rows(machine)] == ["prp_one", "prp_three", "prp_two"]
    assert _rows(machine) == _expected(machine)


def test_an_index_dropped_by_another_store_is_found_at_its_path(machine):
    """Matrix "Dropped by another store"."""
    _write(machine, ledger(pending(commitment("prp_one"))))
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
        assert [row.commitment_id for row in _rows(machine)] == ["prp_one"]
        machine.storage.drop_commitment_index()  # a second store: the terminal's
        assert not _index_path(machine).exists()
        _write(machine, ledger(pending(commitment("prp_one")), pending(commitment("prp_two"))))
        serving.on_loop(lambda: serving.indexing.request(PERSONAL))
        serving.settle()
    finally:
        serving.close()
    assert [row.commitment_id for row in machine.storage.commitment_index_rows()] == ["prp_one", "prp_two"]


def test_a_kill_inside_the_replace_is_rolled_back_and_derived_at_the_next_start(machine, tmp_path):
    """Matrix "Kill mid-step"."""
    _write(machine, ledger(pending(commitment("prp_one"))))
    _derived_at_start(machine, PERSONAL)
    kept = _rows(machine)
    _write(machine, ledger(pending(commitment("prp_one")), pending(commitment("prp_two"))))
    script = tmp_path / "killed.py"
    script.write_text(
        "import os, sqlite3, sys\n"
        "db = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
        "db.execute('BEGIN IMMEDIATE')\n"
        "db.execute(\"UPDATE commitments SET state = 'broken', source_md5 = 'x'\")\n"
        "os._exit(9)\n"
    )
    subprocess.run([sys.executable, str(script), str(_index_path(machine))], check=False)
    assert _rows(machine) == kept
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _rows(machine) == _expected(machine)


def test_an_append_before_the_loop_is_counted_and_covered_by_the_start_check(machine, caplog):
    """Matrix "Request before the loop"; the wiring slice test's second case."""
    _, ids = _summarised(machine)

    def append_before_lifespan(serving: Serving) -> None:
        # Through the daemon's own store, whose hook is set and whose loop is not yet.
        serving.storage.append_commitments([pending(commitment(ids[0]))], scope=PERSONAL, after=0)

    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine, catch_up=False, before=append_before_lifespan)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _start_line(caplog).endswith(", 1 requests before start")
    assert [row.commitment_id for row in _rows(machine)] == [ids[0]]


def test_shutdown_clears_the_hook_first_and_counts_what_was_waiting(machine, monkeypatch, caplog):
    """Matrix "Shutdown with requests waiting"; the wiring slice test's third case."""
    _write(machine, ledger(pending(commitment("prp_one"))))
    caplog.set_level(logging.INFO, logger=LOGGER)
    serving = _serving(machine, catch_up=False)
    released = threading.Event()
    real = indexing_module.derive

    def slow(storage, scope, **kwargs):
        released.wait(5)
        return real(storage, scope, **kwargs)

    storage = serving.storage
    try:
        serving.settle()
        personal_before = _rows(machine, PERSONAL)
        # Both stale: personal is taken and held at its read; alpha waits behind it.
        _write(machine, ledger(pending(commitment("prp_one")), pending(commitment("prp_two"))))
        _write(machine, ledger(pending(commitment("prp_alpha_one"))), ALPHA)
        monkeypatch.setattr(indexing_module, "derive", slow)
        serving.on_loop(lambda: [serving.indexing.request(PERSONAL), serving.indexing.request(ALPHA)])
        time.sleep(0.1)
    finally:
        threading.Timer(0.3, released.set).start()
        serving.close()
    assert storage._commitments_hook is None  # noqa: SLF001 — cleared at shutdown
    assert "commitment index: 1 requested scope(s) not derived at shutdown; the next start checks them" in _messages(caplog)
    # The held derivation was abandoned before its replace; alpha was never taken.
    assert _rows(machine, PERSONAL) == personal_before
    assert _rows(machine, ALPHA) == ()
    assert not any("not derived (_Abandoned)" in message for message in _messages(caplog))
    # An append after the hook was cleared starts nothing.
    storage.append_commitments([pending(commitment("prp_after"))], scope=PERSONAL, after=_ledger_path(machine).stat().st_size)
    assert _rows(machine, PERSONAL) == personal_before
    # The next start checks.
    monkeypatch.setattr(indexing_module, "derive", real)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
    finally:
        serving.close()
    assert [row.commitment_id for row in _rows(machine, ALPHA)] == ["prp_alpha_one"]
    assert _rows(machine, PERSONAL) == _expected(machine, PERSONAL)


def test_a_hook_that_raises_never_fails_the_append(machine, caplog):
    """Matrix "Hook raises"."""

    def raising(scope):
        raise RuntimeError("never in a log")

    machine.storage.on_commitments_appended(raising)
    caplog.set_level(logging.WARNING)
    size = machine.storage.append_commitments([pending(commitment("prp_one"))], scope=PERSONAL, after=0)
    assert size == _ledger_path(machine).stat().st_size
    warned = [m for m in _messages(caplog) if m.startswith("commitment index: the request after an append")]
    assert warned == [
        "commitment index: the request after an append to personal failed (RuntimeError); "
        "the next append or start derives it"
    ]


def test_with_no_catch_up_task_the_check_runs_at_once(machine):
    """Matrix "No catch-up task"."""
    _write(machine, ledger(pending(commitment("prp_one"))))
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _rows(machine) == _expected(machine)


def test_a_failed_catch_up_still_lets_the_check_run(machine, monkeypatch):
    """Matrix "Catch-up failed"."""
    from pm_ai.app.commitments import CommitmentCatchUp

    async def failed(self):
        raise RuntimeError("the catch-up failed")

    monkeypatch.setattr(CommitmentCatchUp, "run", failed)
    _write(machine, ledger(pending(commitment("prp_one"))))
    serving = _serving(machine)
    try:
        serving.settle()
    finally:
        serving.close()
    assert _rows(machine) == _expected(machine)


def test_the_index_is_written_with_no_master_key(machine, monkeypatch):
    """Matrix "Key absent": the index asks for no key."""
    from pm_ai.ports import KeyNotFound

    asked: list[str] = []

    class NoKey:
        """A crypto with no master key: any use of it is refused, and noted."""

        def __getattr__(self, name):
            asked.append(name)
            raise KeyNotFound("no master key is enrolled")

    monkeypatch.setattr(machine.storage, "_crypto", NoKey())
    _write(machine, ledger(pending(commitment("prp_one"))))
    derive(machine.storage, PERSONAL)
    assert [row.commitment_id for row in _rows(machine)] == ["prp_one"]
    assert asked == [], "the index asked the crypto for nothing"


def test_reading_an_absent_index_opens_and_creates_nothing(machine, monkeypatch):
    """Matrix "Read of an absent index"."""
    opened: list[object] = []
    monkeypatch.setattr(index_store.sqlite3, "connect", lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(AssertionError))
    assert machine.storage.commitment_index_rows() == ()
    assert machine.storage.commitment_index_checksums() == {}
    assert opened == [] and not _index_path(machine).exists()


def test_the_people_scope_is_never_derived_or_indexed(machine):
    """Matrix "People scope"."""
    people = DataScope(ScopeKind.PEOPLE, person_id="example")
    with pytest.raises(NotACommitmentScope):
        derive(machine.storage, people)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle()
        serving.on_loop(lambda: serving.indexing.request(people))
        serving.settle()
        assert serving.indexing.done == [] or all(each.scope != str(people) for each in serving.indexing.done)
    finally:
        serving.close()
    assert not _index_path(machine).exists()


def test_the_new_storage_calls_are_classified(machine):
    """The storage-call classification covers 15b's six calls, the writes asking no `git`."""
    from pm_ai.app.wiring import HANDED_CALLS, NEVER_FROM_A_JOB, PLAIN_READS, _asks_none

    for name in ("replace_commitment_rows", "remove_commitment_scopes", "drop_commitment_index"):
        assert HANDED_CALLS[name][1] is _asks_none
    assert {"commitment_index_rows", "commitment_index_checksums"} <= PLAIN_READS
    assert "on_commitments_appended" in NEVER_FROM_A_JOB
    from pm_ai.ports import StoragePort

    assert not hasattr(StoragePort, "on_commitments_appended")


# ── Time ─────────────────────────────────────────────────────────────────────


def test_a_daemon_append_on_a_5000_line_project_ledger_is_current_within_2_seconds(machine):
    """Matrix "Timed trigger"."""
    _, ids = _summarised(machine, scope=ALPHA)
    _fill(machine, ALPHA, ids[0], 5000)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle(timeout=30)
        started = time.monotonic()
        serving.prompt(f"proposal approve {ids[0]}")
        deadline = started + 2.0
        while time.monotonic() < deadline:
            held = machine.storage.commitment_index_checksums().get("project:alpha")
            if held is not None and held.md5s == {_md5(machine, ALPHA)} and held.rows == 5001:
                break
            time.sleep(0.01)
        took = time.monotonic() - started
    finally:
        serving.close()
    assert took < 2.0, f"{took:.2f} s"


def test_the_start_check_over_two_stale_5000_line_ledgers_finishes_within_2_seconds(machine, monkeypatch):
    """Matrix "Timed start": the daemon's own check, on its pool and writer thread."""
    _, ids = _summarised(machine)
    _fill(machine, PERSONAL, ids[0], 5000)
    _fill(machine, ALPHA, ids[0], 5000)
    took: list[float] = []
    real = CommitmentIndexing._start_check

    def timed(self):
        started = time.monotonic()
        try:
            return real(self)
        finally:
            took.append(time.monotonic() - started)

    monkeypatch.setattr(CommitmentIndexing, "_start_check", timed)
    serving = _serving(machine, catch_up=False)
    try:
        serving.settle(timeout=30)
        report = serving.indexing.last_check
    finally:
        serving.close()
    assert report is not None and report.count("derived") == 2
    assert took and took[0] < 2.0, f"{took[0]:.2f} s"


def _lines_of(n: int, *, prefix: str) -> bytes:
    return ledger(*(pending(commitment(f"prp_{prefix}_{i:05d}")) for i in range(n)))


@pytest.mark.parametrize("count", [5000, 50000])
def test_the_claimed_step_stays_under_half_a_second(machine, count, capsys):
    """Matrix "Claimed step": one commitment changed, and the scope derived whole."""
    _write(machine, _lines_of(count, prefix="big"))
    data = _ledger_path(machine).read_bytes()
    started = time.monotonic()
    rows = index_rows("personal", fold(parse_ledger(data)), source_md5(data))
    folded = time.monotonic() - started
    started = time.monotonic()
    machine.storage.replace_commitment_rows(PERSONAL, rows, frozenset())
    whole = time.monotonic() - started
    from commitment_index_fixtures import marked
    from pm_ai.domain.commitments import Mark

    data += ledger(marked("prp_big_00007", Mark.FULFILLED, after(1)))
    changed_rows = index_rows("personal", fold(parse_ledger(data)), source_md5(data))
    held = machine.storage.commitment_index_checksums()["personal"].md5s
    started = time.monotonic()
    replaced = machine.storage.replace_commitment_rows(PERSONAL, changed_rows, held)
    one = time.monotonic() - started
    with capsys.disabled():
        print(
            f"\n15b claimed step at {count} lines: whole {whole:.3f} s, one changed {one:.3f} s; "
            f"read and fold off the writer thread {folded:.3f} s"
        )
    assert replaced.written == 1
    assert whole < 0.5 and one < 0.5, (whole, one)
