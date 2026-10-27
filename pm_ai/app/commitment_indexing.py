"""What starts the commitment index's derivations — temporary; story 10a replaces
this file (story 15b).

**An in-memory task and a change event from the write path, which AD-45 and
AD-46 forbid** ("every job is a queue row"; "no change events from its own
write path"). It is here because story 16 needs `commitment_index.db` before
the watcher and the durable queue exist (Andrei, 2026-10-24): until 10a the
serving daemon derives a scope's rows again after each append it makes to
that scope's `commitments_log.md`, and checks every scope once at start. 10a
deletes this file whole, and the storage hook `on_commitments_appended` with
it; it keeps `pm_ai.core.commitment_index` (the rows, `derive`, the start
check, the 1h operations), the schema and the stored checksums.

## What it does

- **The hook.** `attach_background` gives the daemon's store one callback; it
  fires on the writer thread after each append past its size check, and does
  one thing: `loop.call_soon_threadsafe(self.request, scope)`. The requested
  scopes are touched only on the daemon's event loop. Before the loop is
  attached it only counts.
- **The start check**, once, after 15a's catch-up has finished
  (`asyncio.wait`, whatever its outcome), on the I/O pool: the scope set is
  the one `open_world` reads at start; every stale scope is derived, an
  unusable index dropped and rebuilt, rows of projects positively gone from
  a readable registry removed.
- **The requests**, then: an ordered set, first in, first out, one derivation
  at a time; a scope leaves the set before its read, so a request during its
  derivation derives it once more. A derivation that fails is logged by type
  and the next scope is taken: the task ends only when cancelled.
- **Appends a terminal process makes, and hand edits, start nothing**
  (Andrei, 2026-10-27): they are derived at the daemon's next append to that
  scope, or at its next start.
- **Shutdown** clears the hook first, then cancels the task: the requested
  scopes not derived are counted in one line, and a replace already handed to
  the writer thread runs to its end.

Nothing here writes a ledger or takes a ledger's claim; the index is Tier 3,
which nothing watches, so a derivation cannot request itself (AD-45).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pm_ai.app.prompts import DAEMON_LOGGER
from pm_ai.app.proposal_reads import open_world
from pm_ai.core.commitment_index import (
    CheckReport,
    Derivation,
    check,
    derive,
    drop_commitment_index,
    rebuild_commitment_index,
)
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import CommitmentIndexUnusable

__all__ = ["INDEX_STEP", "CommitmentIndexing"]

_log = logging.getLogger(DAEMON_LOGGER)

INDEX_STEP = "derive the commitment index"
"""The writer step's name for the index's claimed steps."""


class _Abandoned(BaseException):
    """The daemon is stopping: a derivation not yet at its replace is abandoned.

    A `BaseException`, as a cancellation is, so no `except Exception` on the
    way out reads it as a failure, a latch or an unusable index.
    """


class CommitmentIndexing:
    """The indexer task `_lifespan` runs in the serving daemon (story 15b; temporary)."""

    def __init__(self, *, requests: Any, now: Callable[[], datetime]) -> None:
        self.requests = requests
        self.now = now
        self._requested: dict[str, DataScope] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._early = 0
        self._stopping = threading.Event()
        self._keys: frozenset[str] | None = None
        self._scopes: tuple[DataScope, ...] = ()
        self._roots: dict[str, Path] = {}
        self._latched: str | None = None
        self._deriving = False
        self.last_check: CheckReport | None = None
        self.checked = 0
        """How many start checks finished (one per run)."""
        self.done: list[Derivation] = []
        """Every derivation this run made, in order."""

    # ── The hook (the writer thread) ─────────────────────────────────────────

    @property
    def _storage(self) -> Any:
        return self.requests.daemon.storage

    def attach(self) -> None:
        """Give the daemon's store the hook. The serve path's, through `attach_background`."""
        self._storage.on_commitments_appended(self.hook)

    def stop(self) -> None:
        """Clear the hook, so an append from now on starts nothing; abandon what is unhanded."""
        self._storage.on_commitments_appended(None)
        self._stopping.set()

    def hook(self, scope: DataScope) -> None:
        """After an append to `scope`'s ledger: post a request to the loop, nothing else."""
        loop = self._loop
        if loop is None:
            self._early += 1
            return
        loop.call_soon_threadsafe(self.request, scope)

    # ── The requests (the event loop) ────────────────────────────────────────

    def request(self, scope: DataScope) -> None:
        """Ask for `scope` to be derived; a scope already asked for keeps its turn."""
        self._requested.setdefault(str(scope), scope)
        if self._wake is not None:
            self._wake.set()

    @property
    def requested(self) -> tuple[str, ...]:
        """The scopes asked for and not yet taken, first in first."""
        return tuple(self._requested)

    @property
    def idle(self) -> bool:
        """The start check finished, nothing is asked for, and no derivation is running."""
        return self.checked > 0 and not self._requested and not self._deriving

    async def run(self, catch_up: asyncio.Task[Any] | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        try:
            if catch_up is not None:
                # Whatever its outcome: its exception or cancellation is its own, logged.
                await asyncio.wait({catch_up})
            try:
                await self.requests.pool.run(self._start_check)
            except asyncio.CancelledError:
                raise
            except _Abandoned:
                pass
            except Exception as unexpected:  # noqa: BLE001 — logged; the next start tries again
                _log.error(
                    "commitment index at start stopped on an unexpected %s; the next start tries again",
                    type(unexpected).__name__,
                )
            finally:
                self.checked += 1
            while True:
                while self._requested:
                    key = next(iter(self._requested))
                    scope = self._requested.pop(key)
                    self._deriving = True
                    try:
                        await self._derive(scope)
                    finally:
                        self._deriving = False
                self._wake.clear()
                await self._wake.wait()
        except asyncio.CancelledError:
            waiting = len(self._requested)
            self._requested.clear()
            if waiting:
                _log.info(
                    "commitment index: %d requested scope(s) not derived at shutdown; "
                    "the next start checks them",
                    waiting,
                )
            raise

    async def _derive(self, scope: DataScope) -> None:
        key = str(scope)
        if self._keys is None or key not in self._keys or self._latched is not None:
            return
        try:
            found = await self.requests.pool.run(
                lambda: derive(self._storage, scope, step=self._step, present=self._present)
            )
        except asyncio.CancelledError:
            raise
        except _Abandoned:
            return
        except CommitmentIndexUnusable as unusable:
            reason = unusable.reason
            try:
                await self.requests.pool.run(lambda: self._recover(reason))
            except _Abandoned:
                pass
            return
        except Exception as failed:  # noqa: BLE001 — the indexer never dies
            self._failed(key, type(failed).__name__)
            return
        self._report(found, "trigger")
        if found.outcome == "changed":
            self.request(scope)

    # ── The steps (the I/O pool) ─────────────────────────────────────────────

    def _step(self, work: Callable[[], Any]) -> Any:
        if self._stopping.is_set():
            raise _Abandoned("the daemon is stopping")
        return self.requests.writer.call(work, label=INDEX_STEP)

    def _present(self, scope: DataScope) -> bool:
        if scope.kind is not ScopeKind.PROJECT:
            return True
        root = self._roots.get(str(scope.project_id))
        return root is not None and root.is_dir()

    def _start_check(self) -> None:
        storage = self._storage
        paths = storage.paths if isinstance(storage.paths, ScopePaths) else None
        world = open_world(paths, self.now())
        self._roots = dict(world.projects)
        self._scopes = tuple(world.scopes)
        self._keys = frozenset(str(scope) for scope in self._scopes)
        report = check(
            storage,
            self._scopes,
            registry_readable=world.registry_fault is None,
            step=self._step,
            present=self._present,
        )
        self.last_check = report
        for found in report.derivations:
            self._report(found, "start")
            if found.outcome == "changed" and self._loop is not None:
                self._loop.call_soon_threadsafe(self.request, DataScope.parse(found.scope))
        _log.info(
            "commitment index at start: %d current, %d derived again, %d scopes removed, "
            "%d unreadable, %d requests before start",
            report.count("current"),
            report.count("derived"),
            len(report.removed),
            report.count("unreadable"),
            self._early,
        )
        if report.unusable is not None and report.latched is None:
            _log.warning("the index was unusable (%s); dropped and rebuilt", report.unusable)
        if report.latched is not None:
            self._latch(report.latched)
        if report.registry_unreadable:
            _log.warning("scopes not removed: the projects registry could not be read")
        if report.remove_failed is not None:
            _log.warning(
                "commitment index: rows of dropped projects not removed (%s); the next start tries again",
                report.remove_failed,
            )

    def _recover(self, reason: str) -> None:
        """An index found unusable mid-run: dropped and rebuilt once, or latched."""
        try:
            drop_commitment_index(self._storage, step=self._step)
            rebuilt = rebuild_commitment_index(
                self._storage, self._scopes, step=self._step, present=self._present
            )
        except Exception as failed:  # noqa: BLE001 — latched until the next start
            self._latch(type(failed).__name__)
            return
        _log.warning("the index was unusable (%s); dropped and rebuilt", reason)
        for found in rebuilt.derivations:
            self._report(found, "trigger")
            if found.outcome == "changed" and self._loop is not None:
                self._loop.call_soon_threadsafe(self.request, DataScope.parse(found.scope))

    def _latch(self, failure: str) -> None:
        self._latched = failure
        _log.warning(
            "commitment index unusable and not rebuilt (%s); derivations skipped until the next start",
            failure,
        )

    # ── What is logged ───────────────────────────────────────────────────────

    def _report(self, found: Derivation, trigger: str) -> None:
        self.done.append(found)
        if found.outcome == "derived":
            _log.info(
                "commitment index: %s derived again (%s), %d rows written, %d removed",
                found.scope,
                trigger,
                found.written,
                found.removed,
            )
        elif found.outcome == "changed":
            _log.info("commitment index: %s changed meanwhile; derived again", found.scope)
        elif found.outcome in ("unreadable", "failed"):
            self._failed(found.scope, found.failure or "Exception")

    @staticmethod
    def _failed(scope: str, failure: str) -> None:
        _log.warning(
            "commitment index: %s not derived (%s); the next append or start tries again",
            scope,
            failure,
        )
