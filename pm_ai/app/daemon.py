"""`pm-ai daemon run`: the long-lived daemon, in the foreground (AD-7, AD-8, story 4e).

The composition root owns the lifecycle (AD-30), so this is where the daemon
binds, takes its token, serves and stops. What it serves is built in
`pm_ai.surfaces.api.app`; what it serves *with* is the `Daemon` that
`pm_ai.app.entry` composed once for this process.

## The order, and why it is this one

1. **The runtime extra.** `fastapi` and `uvicorn` are an install extra, not a
   default. Without them the start is refused, naming the extra.
2. **The port.** `127.0.0.1` and nothing else, on `daemon_port`. A port already
   taken refuses the start naming the port and the key. Taken *before* the
   token is touched: that is what keeps the daemon single — a second
   `daemon run` meets the first one's port — and what keeps the loser of two
   simultaneous starts from writing anything.
3. **The token.** Read if present, minted if absent, with an exclusive create so
   a file that appeared meanwhile is read rather than replaced. A file that is
   unusable refuses the start naming it, and is never minted over.
4. **Serve**, until Ctrl-C, a stop signal, or the terminal closing (`SIGINT`,
   `SIGTERM`, `SIGHUP` — all three are a stop, none is re-raised afterwards).
5. **Stop**: stop listening, cancel the timers, mark unfinished jobs, close
   the writer, return 0. A clean stop says so in one line (story 4s): in
   `daemon.log` before the log is closed, and on standard output — where the
   listening line went — after the store is closed, naming the first stop
   request and how many unfinished jobs were marked failed.

Story 4r put two steps before the port and two before serving: the writer
thread starts and reopens `operational.db` as the daemon's own connection, and
asks the keychain for the master key — once, on the writer thread (AD-5). A
locked keychain or a key that cannot be read refuses the start, naming it; a
key not yet enrolled does not, and every encrypted step is refused instead.
After the port and the token, the restart pass marks every job an earlier run
left unfinished `failed`, and jobs older than seven days are removed.

Story 9a put one step between the port and the writer: the per-user lifetime
lock on `~/.pm-ai/private/daemon.lock` (AD-7), held until the process ends. A
second daemon reading another `daemon_port` binds its port and is refused
there, naming the file, before its writer thread exists or any timer is
attached. It is not an AD-5 claim, and no command takes it.

Story 1s put the start-up mode walk right after it, before the writer thread
starts: both homes and every loaded project's `memory/` and `transcripts/`
are narrowed to owner-only (the old mode minus the group and other bits), each
change and refusal is a line in `daemon.log`, and one console line counts them.
It is not a write in AD-5's sense, is bounded by a time budget, and never
refuses the start.

## One loop, one writer thread, one I/O pool (AD-5, AD-19, story 4r)

The daemon composed once, before this module was reached. The event loop only
waits and schedules: every write and every claim is a claimed step on the one
writer thread (`pm_ai.app.concurrency.WriterThread`), and everything else that
blocks — the status probes, every command a client sends, a job's bounded steps
— runs on the I/O pool of four. The request log line goes on a queue the loop
never waits on, and one listener thread writes it to the file. Background work
(`9a`'s timers, `9b`'s render) attaches in `_lifespan`, on this loop, and hands
its steps to the same writer thread and pool.

## Stopping (AD-5, cited by `9a` and `9b`)

Stop listening; cancel the timers and end the open event streams. From then on
the writer takes no claimed step but a credential save and the shutdown's own
step, which marks every job still unfinished `failed`, naming the last claimed
step each finished. It finishes the step it is running and every step already
queued, with no time limit; a job's later step is refused and logged by name.
The wait for the writer is the last thing `serve` does before closing the
store — in its `finally`, never in the application's lifespan, where a second
Ctrl-C would skip it. A stop on a schema-version mismatch skips the marking and
refuses with exit code 3.

## The thin clients still write

In this wave `pm-ai dashboard`, `connector add`, `goal set` and the rest keep
writing directly, serialized by AD-5's one cross-process claim; this daemon is
one more writer under the same claim, until story 10 makes it the sole writer.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import io
import json
import logging
import logging.handlers
import os
import queue
import secrets
import signal
import socket
import sys
import threading
import traceback
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType
from typing import Any

from pm_ai.app.commitments import CommitmentCatchUp
from pm_ai.app.commitment_indexing import CommitmentIndexing
from pm_ai.app.concurrency import IOPool, ModelPool, WriterThread
from pm_ai.app.prompts import (
    DAEMON_LOG_NAME,
    DAEMON_LOGGER,
    SCHEMA_MISMATCH_EVENT,
    WRITER_HALT_EVENT,
    RequestPath,
)
from pm_ai.app.ticks import HarvestTimers, ProposalSweep, RenderTimers
from pm_ai.app.pipelines import PoolNotWired, SummaryJobs
from pm_ai.app.wiring import Daemon, frontier_key_check, handed_daemon
from pm_ai.domain.api_token import TOKEN_BYTES, ApiTokenRefused
from pm_ai.domain.health import Report
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.domain.storage_tiers import project_private_dirs
from pm_ai.platform.claims import LifetimeLock, LifetimeLockHeld, hold_for_life
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import DaemonRefused, KeychainUnavailable
from pm_ai.storage.crypto import LazyKeyCrypto
from pm_ai.storage.service import (
    ModeCalls,
    ModeWalk,
    OperationalStoreStuck,
    OperationalStoreUnavailable,
    SchemaVersionChanged,
)
from pm_ai.surfaces.api.app import REQUEST_LOGGER, create_app
from pm_ai.surfaces.cli.dispatch import (
    RESTART_SENTENCE,
    HealthReport,
)

__all__ = ["LOOPBACK", "REQUEST_LOG", "bind_loopback", "serve", "status_body", "take_token"]

LOOPBACK = "127.0.0.1"
"""The one address the daemon listens on (AD-2). Never a wildcard, never another interface."""

REQUEST_LOG = DAEMON_LOG_NAME
"""The file under `~/.pm-ai/logs/` the request log rotates through (AD-24) —
the one the daemon's own diagnostic lines go to and the restart pass reads."""

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
"""Ctrl-C, a stop signal, and the terminal closing — each a clean stop, exit 0."""

_PORT_KEY = "daemon_port"

NO_KEY_ENROLLED = (
    "no master key was enrolled when the daemon started, so it reads and writes "
    "no encrypted file; run `pm-ai key enrol`. " + RESTART_SENTENCE
)
"""What every encrypted step is refused with when the start found no key (AD-5)."""

_log = logging.getLogger(DAEMON_LOGGER)


def _no_diagnosis(itself: str) -> HealthReport:
    raise DaemonRefused("no diagnosis was composed for this daemon")


def serve(
    daemon: Daemon,
    *,
    probes: Callable[[], Report],
    announce: Callable[[str], None] | None = None,
    diagnose: Callable[[str], HealthReport] = _no_diagnosis,
    bounds: Mapping[tuple[str, ...], Callable[[Daemon], float]] | None = None,
) -> int:
    """Run the daemon in the foreground until it is told to stop; return 0.

    `probes` is `run_all` over what the daemon holds, bound by the composition
    root — `GET /v1/status` reports it without a `daemon` probe, because this
    process is the daemon and never asks itself. `announce` is where the one
    line saying where it listens goes. `diagnose` is `doctor`'s report for a
    line sent through the daemon, handed the daemon's own words about itself;
    `bounds` every command's declared
    bound (AD-21).

    Raises `DaemonRefused` for every reason it will not start, each naming its
    cause, before anything is served — and for a stop on a schema-version
    mismatch or a stuck writer, which is a refusal too (exit code 3).
    """
    uvicorn = _require_runtime_extra()
    port = daemon.config.daemon_port
    # First, so a step refused at start and a start with no key enrolled are
    # in the diagnostic log, not only on the console (AD-24). A log that cannot
    # be opened still closes the store.
    try:
        request_log = _RequestLog.attach(daemon)
    except BaseException:
        daemon.storage.close()
        raise
    # Filled only by a clean stop (story 4s): the halt, a refused start and an
    # unexpected error leave it empty and print what they print today.
    stop_line: list[str] = []
    try:
        code = _serve(
            daemon, uvicorn, port,
            probes=probes, announce=announce, diagnose=diagnose,
            bounds=bounds or {}, stop_line=stop_line,
        )
    except DaemonRefused:
        raise
    except Exception as unexpected:
        _log.error("the daemon stopped on an unexpected %s", type(unexpected).__name__)
        raise
    finally:
        try:
            if stop_line:
                # Before the log is closed, without the store's part.
                _log.info("%s.", stop_line[0])
            request_log.detach()
        finally:
            daemon.storage.close()
    # After the store is closed: a close that raised skipped this line.
    if stop_line:
        say_stopped(f"pm-ai: {stop_line[0]}; the store is closed.")
    return code


STOP_NAMES: Mapping[int, str] = {
    signal.SIGINT: "Ctrl-C",
    signal.SIGTERM: "a stop signal (SIGTERM)",
    signal.SIGHUP: "the terminal closing (SIGHUP)",
}
"""How the stop line names the first stop request it got (story 4s)."""


def stop_words(first: int | None, *, marked: int | None, unmarked: str | None) -> str:
    """The clean stop's words, without the store's part (story 4s).

    `first` is the first stop request the server saw; `marked` how many jobs —
    commands sent to the daemon — the stop marked failed; `unmarked` the type
    of the error the marking failed with, which leaves them to the next start.
    Renders and harvests marked by the same step are in `daemon.log` already
    and are not counted.
    """
    cause = STOP_NAMES.get(first, "") if first is not None else ""
    said = f"the daemon stopped cleanly on {cause}" if cause else "the daemon stopped cleanly"
    if unmarked is not None:
        return (
            f"{said}; unfinished jobs were not marked failed ({unmarked}), so the "
            f"next start marks them"
        )
    if not marked:
        return f"{said}; no job was left unfinished"
    noun = "job" if marked == 1 else "jobs"
    return f"{said}; {marked} unfinished {noun} marked failed"


def say_stopped(line: str) -> None:
    """The stop line on standard output, never a failure of the stop itself (4s).

    A console that can no longer be written to — the terminal closed, the
    pipe's reader gone — does not turn a clean stop into a non-zero exit. The
    line goes to the console unbuffered, so a write that fails leaves nothing
    behind for the interpreter's own flush at exit to fail on again; the error
    is ignored and standard output is pointed at nothing. The line is in
    `daemon.log` either way.
    """
    stream: Any = sys.stdout
    try:
        stream.flush()
    except (OSError, ValueError):
        # Bytes still buffered would fail again at the interpreter's flush.
        sys.stdout = _Nothing()
        return
    try:
        raw = stream.buffer.raw  # the console itself, beneath both buffers
        encoding = stream.encoding or "utf-8"
    except AttributeError:
        raw = None
        encoding = "utf-8"
    try:
        if raw is None:
            # A stream with no console beneath it (a test's capture).
            print(line, flush=True)
            return
        data = memoryview(f"{line}\n".encode(encoding, "replace"))
        idle = 0
        while data and idle < _IDLE_WRITES:
            # A raw write may take part of the line, or none of it (`None`, a
            # console that would block); a console that keeps taking nothing
            # is given up on rather than waited on for ever.
            taken = raw.write(data) or 0
            idle = 0 if taken else idle + 1
            data = data[taken:]
    except (OSError, ValueError):
        sys.stdout = _Nothing()


_IDLE_WRITES = 100
"""How many writes in a row may take nothing before the stop line is given up on."""


class _Nothing(io.TextIOBase):
    """Standard output once the console has gone: every write is accepted and dropped."""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        return len(text)


def _serve(
    daemon: Daemon,
    uvicorn: Any,
    port: int,
    *,
    probes: Callable[[], Report],
    announce: Callable[[str], None] | None,
    diagnose: Callable[[str], HealthReport],
    bounds: Mapping[tuple[str, ...], Callable[[Daemon], float]],
    stop_line: list[str],
) -> int:
    server_holder: list[Any] = []

    # The port first: a second `daemon run` meets the first one's listener and
    # is refused before its writer opens the store or the keychain is asked.
    listener = bind_loopback(port)
    # Then the per-user lifetime lock (AD-7, story 9a), before the writer
    # thread exists and before any timer: a second daemon reading another
    # `daemon_port` binds its port and is refused here, naming the file.
    try:
        lifetime = take_lifetime_lock(daemon)
    except BaseException:
        listener.close()
        raise
    try:
        # Then the start-up mode walk (story 1s, AD-5, AD-6): after the lock,
        # before the writer thread exists or any request is served. Never a
        # refusal — whatever it cannot change is logged and the start goes on.
        tighten_private_modes(daemon, announce=announce or _announce)
        return _serve_locked(
            daemon, uvicorn, port, listener,
            probes=probes, announce=announce, diagnose=diagnose, bounds=bounds,
            server_holder=server_holder, stop_line=stop_line,
        )
    finally:
        # Released when the daemon is done, which is when the process ends.
        lifetime.release()


MODE_WALK_BUDGET_SECONDS = 10.0
"""The start-up mode walk's time budget (story 1s).

Measured 2026-10-17 by `tests/architecture/test_private_modes.py`
(`test_the_walks_cost_on_a_generated_home`): 5,000 files in 50 folders took
0.32 s to tighten and 0.22 s to walk once already private, on the development
Mac (macOS 26.6, 12 cores). Ten seconds is therefore about thirty times that
home — room for a person's own repository kept in `~/.manager-ai`. On overrun
the walk stops, logs where, and the next start walks again.
"""


def _private_roots(daemon: Daemon) -> tuple[list[tuple[str, Path]], list[Path]]:
    """Every root the walk covers, labelled, and the repositories the homes skip.

    The two homes — `application` (the team-member scope inside it) and
    `personal` — and each loaded project's `memory/` and `transcripts/`,
    labelled by its project id. A registered repository inside a home (the
    home root itself included) is skipped by the home's walk, and its private
    folders are roots of their own whether or not the project is loaded, since
    the home's walk would otherwise have covered them. A registered repository
    outside the homes is walked only when it is loaded.
    """
    paths = daemon.storage.paths
    if not isinstance(paths, ScopePaths):
        return [], []
    roots: list[tuple[str, Path]] = [
        ("application", paths.application_root),
        ("personal", paths.personal_root),
    ]
    homes = (paths.application_root, paths.personal_root)
    repositories: list[Path] = list(paths.project_roots.values())
    inside_a_home = [
        project
        for project, repository in paths.project_roots.items()
        if any(_within(repository, home) for home in homes)
    ]
    walked: set[str] = set()
    for project in (*daemon.watched, *inside_a_home):
        if project in walked:
            continue
        walked.add(project)
        scope = DataScope(ScopeKind.PROJECT, project)
        try:
            repositories.append(paths.repository(project))
            for artifact in project_private_dirs():
                roots.append((project, paths.resolve(scope, artifact)))
        except ScopeResolutionError:
            continue
    return roots, repositories


def _within(path: Path, home: Path) -> bool:
    """Whether `path` is `home` or inside it, as written or once links are resolved."""
    if path == home or path.is_relative_to(home):
        return True
    try:
        real, real_home = path.resolve(), home.resolve()
    except OSError:
        return False
    return real == real_home or real.is_relative_to(real_home)


def _log_path(daemon: Daemon) -> str:
    """Where `daemon.log` is, with `~` for the home, for the console line."""
    paths = daemon.storage.paths
    root = paths.application_root if isinstance(paths, ScopePaths) else None
    if root is None:
        return f"~/.pm-ai/logs/{REQUEST_LOG}"
    target = root / "logs" / REQUEST_LOG
    home = Path.home()
    if target.is_relative_to(home):
        return f"~/{target.relative_to(home).as_posix()}"
    return str(target)


def tighten_private_modes(
    daemon: Daemon,
    *,
    announce: Callable[[str], None],
    budget: float = MODE_WALK_BUDGET_SECONDS,
    calls: ModeCalls | None = None,
) -> ModeWalk:
    """Tighten what already exists in every private root; log it; one console line.

    Story 1s (AD-6): each mode becomes the old mode minus the group and other
    bits. One `daemon.log` line per path changed, naming its root, the path
    relative to it, and the old and new mode; one per path refused, with the
    reason. Then one line on the console when anything was changed or refused.
    Raises nothing: the daemon starts whatever this finds.
    """
    report = ModeWalk()
    clock = (calls or ModeCalls()).clock
    deadline = clock() + budget
    unreached: list[str] = []
    try:
        roots, repositories = _private_roots(daemon)
    except Exception:  # noqa: BLE001 — tightening never stops the daemon
        _log.warning("the start-up mode walk could not list its roots", exc_info=True)
        roots, repositories = [], []
    for label, root in roots:
        if report.overrun is not None:
            unreached.append(f"{label} {root}")
            continue
        try:
            daemon.storage.tighten_modes(
                root,
                label=label,
                report=report,
                deadline=deadline,
                skip=repositories if label in ("application", "personal") else (),
                calls=calls,
            )
        except Exception:  # noqa: BLE001 — one root failing never stops the others
            _log.warning(
                "the start-up mode walk of %s (%s) stopped; the other roots are "
                "still walked", label, root, exc_info=True,
            )
    for change in report.tightened:
        _log.info(
            "mode tightened: %s %s %04o -> %04o",
            change.root, change.path, change.old, change.new,
        )
    for refusal in report.refused:
        _log.warning("mode not tightened: %s %s: %s", refusal.root, refusal.path, refusal.reason)
    for skipped in report.skipped:
        _log.warning("mode walk: %s", skipped)
    if report.overrun is not None:
        _log.warning(
            "the mode walk stopped at its %ss budget at %s, skipping the rest of "
            "that root%s; what it did not reach is walked at the next start",
            f"{budget:g}", report.overrun,
            f" and {', '.join(unreached)}" if unreached else "",
        )
    if report.tightened or report.refused or report.overrun is not None:
        stopped = (
            "; it stopped at its time budget and the next start walks the rest"
            if report.overrun is not None
            else ""
        )
        announce(
            f"pm-ai: made {_count(report.folders, 'folder')} and "
            f"{_count(report.files, 'file')} owner-only; {len(report.refused)} "
            f"could not be{stopped}. {_log_path(daemon)} names each."
        )
    return report


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def take_lifetime_lock(daemon: Daemon) -> LifetimeLock:
    """The daemon's per-user lifetime lock, or `DaemonRefused` naming the file (AD-7)."""
    where = daemon.storage.daemon_lock_path()
    try:
        # `~/.pm-ai` and `private/` at 0700 when this is the first write there.
        daemon.storage.prepare_private_dir()
        return hold_for_life(where)
    except LifetimeLockHeld:
        raise DaemonRefused(
            f"another pm-ai daemon is running for this user: it holds {where}, "
            f"which one daemon keeps for as long as it runs, whatever port it "
            f"listens on. This one did not start; `pm-ai doctor` says whether "
            f"the other answers."
        ) from None
    except OSError as unusable:
        raise DaemonRefused(
            f"the daemon did not start: its lifetime lock {where} could not be "
            f"taken ({unusable.strerror or type(unusable).__name__}). Nothing "
            f"was started."
        ) from unusable


def _serve_locked(
    daemon: Daemon,
    uvicorn: Any,
    port: int,
    listener: socket.socket,
    *,
    probes: Callable[[], Report],
    announce: Callable[[str], None] | None,
    diagnose: Callable[[str], HealthReport],
    bounds: Mapping[tuple[str, ...], Callable[[Daemon], float]],
    server_holder: list[Any],
    stop_line: list[str],
) -> int:
    def halted(error: BaseException, job: str | None) -> None:
        # On the writer thread, once: the store moved past this daemon's
        # version, or a transaction would not end. Recorded, naming every
        # unfinished job, and the server is asked to stop; nothing more is
        # written.
        try:
            unfinished = daemon.storage.unfinished_request_ids()
        except Exception:  # noqa: BLE001 — the line names the step's job at least
            unfinished = ()
        record_writer_halt(error, job, unfinished=unfinished)
        for server in server_holder:
            server.should_exit = True

    writer = WriterThread(
        log=_log,
        step_scope=daemon.storage.claimed_step,
        halts_on=(SchemaVersionChanged, OperationalStoreStuck),
        on_halt=halted,
    )
    writer.start()
    try:
        writer.call(
            daemon.storage.adopt_daemon_connection,
            label="open the store as the daemon's writer",
        )
        enrolled = _master_key(daemon, writer)
    except BaseException:
        listener.close()
        writer.close()
        raise
    if not enrolled:
        (announce or _announce)(f"pm-ai: {NO_KEY_ENROLLED}")
        _log.warning(NO_KEY_ENROLLED)
    try:
        token = take_token(daemon)
    except BaseException:
        listener.close()
        writer.close()
        raise
    started_at = daemon.clock()
    started = time.monotonic()

    def itself() -> str:
        # The daemon's line in `doctor`, as `daemon_reachable` words it —
        # without asking itself over HTTP.
        return (
            f"running on {LOOPBACK}:{port}, up {int(time.monotonic() - started)}s, "
            f"started at {started_at.astimezone(timezone.utc).isoformat()}"
        )

    pool = IOPool()
    # `7a`: one thread for the local model, started and stopped with the I/O
    # pool; its shutdown closes the connection of a generation still running.
    model_pool = ModelPool(abort=daemon.model.close if daemon.model is not None else None)
    # `7c`: one thread of its own for calls to Claude — the summaries' and the
    # key check's — started and stopped beside the model pool, so the wait for
    # Anthropic holds neither an I/O worker nor the one local slot (AD-19). Its
    # shutdown closes the frontier adapter's connections.
    frontier_pool = ModelPool(
        abort=daemon.router.close if daemon.router is not None else None,
        name="pm-ai-frontier",
    )
    # `9a` and `9b` take both from the daemon (AD-5, AD-19); `7b` the model pool.
    daemon.writer, daemon.pool, daemon.model_pool = writer, pool, model_pool
    daemon.frontier_pool = frontier_pool
    requests = RequestPath(
        daemon=daemon,
        writer=writer,
        pool=pool,
        diagnose=lambda: diagnose(itself()),
        bounds=bounds,
        key_check=frontier_key_check(daemon),
        key_pool=frontier_pool,
    )
    # `7b`: the daemon's summary jobs, on its pools.
    daemon.summaries = summary_jobs(requests, model_pool, frontier_pool)
    try:
        pool.start()
        model_pool.start()
        frontier_pool.start()
        # Before serving, after the port: a second `daemon run` loses the port
        # first, so it can never mark the running daemon's jobs failed.
        try:
            requests.restart_pass()
            requests.prune()
            requests.prune_coverage()  # story 9a; logged, never a refusal
        except (SchemaVersionChanged, OperationalStoreUnavailable, OSError) as refused:
            raise DaemonRefused(
                f"the daemon did not start: settling the jobs an earlier run left "
                f"was refused ({refused}). Nothing was served."
            ) from refused
        app = create_app(
            token=token,
            status=lambda: requests.status(
                lambda: status_body(probes(), started_at=started_at, started=started)
            ),
            lifespan=_lifespan,
            prompt=requests.prompt,
            job=requests.job,
            job_events=requests.job_events,
            frontier_key=requests.frontier_key,
        )
        attach_background(app.state, requests, now=daemon.clock)
        server = _server_class(uvicorn)(
            uvicorn.Config(
                app,
                host=LOOPBACK,
                port=port,
                lifespan="on",
                # Configured by nobody here: uvicorn's own access log would
                # print the client's address and the query string, and the
                # request log above is the one that is allowed to exist.
                log_config=None,
                access_log=False,
                server_header=False,
            )
        )
        # Announced once the stop signals are handled and the application is
        # serving, never before: a supervisor that reads the line and sends a
        # stop must get a clean stop, not the default action of the signal.
        server.announce = lambda: (announce or _announce)(
            f"pm-ai daemon listening on {LOOPBACK}:{port} — in the foreground "
            f"until Ctrl-C or a stop signal."
        )
        # Stopping ends every open event stream first, so the listener's wait
        # for open connections is not a wait on a job that has not finished.
        server.on_stop = requests.events.close_all
        server_holder.append(server)
        try:
            asyncio.run(server.serve(sockets=[listener]))
        except SystemExit as stopped:
            # uvicorn's own `sys.exit` when its startup fails — a lifespan hook
            # that raised, a socket it could not serve on. Never an exit 0.
            raise DaemonRefused(
                f"the daemon bound {LOOPBACK}:{port} but failed to start serving "
                f"(the server stopped during startup with code {stopped.code}); "
                f"its error is printed above. Nothing was served."
            ) from stopped
        except OSError as unservable:
            # On the `sockets=` path uvicorn does not turn a `create_server`
            # failure into its startup exit; it arrives here as itself.
            raise DaemonRefused(
                f"the daemon bound {LOOPBACK}:{port} (`daemon_port`) but could "
                f"not serve on it ({unservable.strerror or unservable}). Nothing "
                f"was served."
            ) from unservable
        if not server.started:
            raise DaemonRefused(
                f"the daemon bound {LOOPBACK}:{port} but failed to start serving; "
                f"its error is printed above. Nothing was served."
            )
    finally:
        # The stop sequence, each part in its own `finally`. Stop listening
        # (uvicorn closed the socket; closing again is harmless). Then the
        # writer: no new step but credential saves and the shutdown's own; the
        # steps already queued finish, with no time limit; still-unfinished
        # jobs are marked failed — skipped on a halt — and the writer closes.
        # Waited for here, at the end of the stop sequence, never in the
        # application's lifespan. A second Ctrl-C during that wait ends the
        # process at once — every claimed step is safe to kill partway (AD-5)
        # — and the parts after it still run where they can.
        drained = _Drained()
        try:
            listener.close()
        finally:
            try:
                # `7a`: a model job queued never starts, and one running loses
                # its connection and its answer, before the writer drains.
                try:
                    model_pool.shutdown()
                finally:
                    # `7c`: a frontier call queued never starts. One running
                    # loses its client: its `sent` line stays, and a `failed`
                    # line, input unknown, follows while the writer still takes
                    # it (an append after the writer closed is refused and
                    # logged, leaving the `sent` line the call's last word).
                    frontier_pool.shutdown()
            finally:
                try:
                    drained = _stop_writer(requests)
                finally:
                    pool.shutdown()
    # A halt — before or during the stop's own marking step — is not a clean
    # stop, and says only what it says today.
    if writer.halted is not None:
        raise DaemonRefused(f"the daemon stopped: {writer.halted}")
    first = server_holder[0].first_stop if server_holder else None
    stop_line.append(stop_words(first, marked=drained.marked, unmarked=drained.unmarked))
    return 0


def summary_jobs(
    requests: RequestPath, model_pool: ModelPool, frontier_pool: ModelPool | None = None
) -> SummaryJobs:
    """The daemon's summary jobs, their stages on its pools (story 7b, AD-19).

    Preparation on the I/O pool, the call on the model pool, what follows a
    call handed back to the I/O pool — no I/O worker waits on the model pool.
    Each automatic job reads the daemon afresh (`requests.fresh`) and hands
    its writes to the writer thread as claimed steps; after the model pool's
    shutdown has begun no answer is handed on. A requested summary's call is
    queued ahead of the automatic ones (Andrei, 2026-10-20).
    """
    writer = requests.writer
    return SummaryJobs(
        io=requests.pool.submit,
        model=model_pool.submit,
        model_first=lambda stage: model_pool.submit(stage, ahead=True),
        daemon_for=lambda: handed_daemon(
            requests.fresh(), hand=lambda work, label: writer.call(work, label=label)
        ),
        model_stopping=lambda: model_pool.stopping,
        # `7c`: a call to Claude on the frontier pool, never the I/O or model pool.
        frontier=_no_frontier_pool if frontier_pool is None else frontier_pool.submit,
        frontier_stopping=(
            (lambda: False) if frontier_pool is None else (lambda: frontier_pool.stopping)
        ),
    )


class FrontierPoolMissing(PoolNotWired):
    """A frontier hop with no frontier pool wired: a wiring error, never run here (7c).

    A call to Claude runs on the frontier pool and nowhere else (AD-19); with
    none given it is refused by name rather than run on the current thread.
    """


def _no_frontier_pool(stage: Callable[[], None]) -> None:
    raise FrontierPoolMissing(
        "a frontier call reached summary jobs built with no frontier pool, so it "
        "was not run: calls to Claude run on the frontier pool only (AD-19). That "
        "is a wiring fault in pm-ai."
    )


class _Drained:
    """What the shutdown's own step did: jobs marked failed, or why it could not (4s)."""

    def __init__(self, marked: int | None = None, unmarked: str | None = None) -> None:
        self.marked = marked
        self.unmarked = unmarked


def record_writer_halt(
    error: BaseException, job: str | None, *, unfinished: Sequence[str] = ()
) -> None:
    """The diagnostic log's line for a writer halt, naming the job (AD-24).

    A schema-version stop is marked so the next start's restart pass reads it
    and marks that job `failed` naming the mismatch and the log rather than
    the ordinary restart reason. Every job still unfinished at the halt is
    named (`jobs`), so each gets that reason, not only the step's own.
    """
    jobs = sorted({*unfinished, *([job] if job else [])})
    if isinstance(error, SchemaVersionChanged):
        found = getattr(error, "found", None)
        _log.error(
            "the operational store's schema version is now %s, not the one the "
            "daemon opened it at; the daemon stopped and wrote nothing more to it",
            found,
            extra={"event": SCHEMA_MISMATCH_EVENT, "found": found, "job": job, "jobs": jobs},
        )
        return
    _log.error(
        "the daemon's writer halted: %s",
        error,
        extra={"event": WRITER_HALT_EVENT, "job": job, "jobs": jobs},
    )


def _stop_writer(requests: RequestPath) -> _Drained:
    writer = requests.writer
    writer.stop_accepting()
    try:
        return _drain(requests)
    except KeyboardInterrupt:
        # A second interrupt while the writer finishes its queue ends the
        # process at once, as a kill would (AD-5): every claimed step is safe
        # to kill partway, and nothing after this — the store's close above
        # all — may run under a writer that is still mid-step.
        print("\npm-ai: interrupted again; stopping at once.", file=sys.stderr, flush=True)
        _exit_now(EXIT_INTERRUPTED)
        return _Drained()  # not reached outside a test that stands in for the exit


EXIT_INTERRUPTED = 3
"""A second interrupt during the stop: the refusal code `main` gives an interrupt."""


def _exit_now(code: int) -> None:
    os._exit(code)


def _drain(requests: RequestPath) -> _Drained:
    """The shutdown's own step, then the writer's close — each waited for, no time limit.

    No `finally`: an interrupt during either wait goes straight to the caller,
    which ends the process rather than wait again. Returns how many jobs the
    step marked failed, or the type of the error it failed with (story 4s).
    """
    writer = requests.writer
    drained = _Drained()
    if writer.halted is None:
        try:
            drained = _Drained(marked=len(requests.shutdown_pass()))
        except Exception as unmarked:  # noqa: BLE001 — the next start's pass marks them
            _log.warning(
                "the shutdown could not mark unfinished jobs failed (%s); the "
                "next start's restart pass will",
                type(unmarked).__name__,
            )
            drained = _Drained(unmarked=type(unmarked).__name__)
    writer.close()
    return drained


def _master_key(daemon: Daemon, writer: WriterThread) -> bool:
    """Ask the keychain for the master key once, on the writer thread (AD-5, AD-6).

    With encryption turned off for the process no key is asked for. A locked
    keychain, or a key it cannot read, refuses the start naming it. A key not
    yet enrolled does not: the daemon runs, and every encrypted step is refused
    naming `pm-ai key enrol`, with no further keychain call. `True` when a key
    is held or none is needed; `False` when none is enrolled.
    """
    crypto = daemon.crypto
    if not isinstance(crypto, LazyKeyCrypto):
        return True
    try:
        enrolled = writer.call(
            lambda: crypto.fetch_once(absent=NO_KEY_ENROLLED),
            label="fetch the master key",
        )
    except KeychainUnavailable as locked:
        raise DaemonRefused(
            f"the daemon did not start: the keychain could not give it the master "
            f"key ({locked}). Unlock the login keychain and start it again; "
            f"nothing listens."
        ) from locked
    except Exception as unusable:  # noqa: BLE001 — named, and the start refused
        raise DaemonRefused(
            f"the daemon did not start: the master key in the keychain could not "
            f"be used ({type(unusable).__name__}). Nothing listens."
        ) from unusable
    return bool(enrolled)


def _announce(line: str) -> None:
    # Flushed, so a terminal or a supervisor reading a pipe sees it at once.
    print(line, flush=True)


def _require_runtime_extra() -> Any:
    """`uvicorn`, after checking the whole web stack imports; or the refusal naming the extra."""
    try:
        import fastapi  # noqa: F401
        import uvicorn
    except ImportError as missing:
        raise DaemonRefused(
            f"`pm-ai daemon run` needs the `runtime` extra, and {missing.name or 'part of it'} "
            f"is not installed. Install it with `uv sync --extra runtime`; "
            f"nothing was started."
        ) from missing
    return uvicorn


def bind_loopback(port: int) -> socket.socket:
    """A listening socket on `127.0.0.1:port`, or `DaemonRefused` naming the port.

    Two checks, because one is not enough on macOS. The bind refuses a second
    listener on `127.0.0.1` itself — which is what keeps the daemon single. But
    `SO_REUSEADDR`, which a restart needs so that connections still in
    `TIME_WAIT` from the last run do not hold the port, also lets this socket
    bind *beside* a foreign service listening on every interface. So the port
    is asked first: anything that accepts a connection on it is something else
    already serving there, and the start is refused before binding.
    """
    if _answers(port):
        raise DaemonRefused(
            _taken(port, "a connection to it was accepted or did not finish, so something holds it")
        )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((LOOPBACK, port))
        listener.listen(128)
    except OSError as refused:
        listener.close()
        if refused.errno == errno.EADDRINUSE:
            raise DaemonRefused(_taken(port, "another process is bound to it")) from refused
        raise DaemonRefused(
            f"pm-ai could not listen on {LOOPBACK}:{port} ({refused}). The port "
            f"is `{_PORT_KEY}` in ~/.pm-ai/config.toml; nothing was started."
        ) from refused
    return listener


def _answers(port: int) -> bool:
    """Whether anything accepts a TCP connection on the loopback port now."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        result = probe.connect_ex((LOOPBACK, port))
    except TimeoutError:
        # Something is there and slow to accept: taken, not free.
        return True
    except OSError:
        return False
    finally:
        probe.close()
    # Refused is the one answer that means nothing listens. A connect still in
    # progress or timed out is something on the port that has not answered yet.
    return result != errno.ECONNREFUSED and (result == 0 or result in _UNFINISHED)


_UNFINISHED = frozenset(
    {errno.EAGAIN, errno.EWOULDBLOCK, errno.EINPROGRESS, errno.EALREADY, errno.ETIMEDOUT}
)


def _taken(port: int, why: str) -> str:
    return (
        f"port {port} on {LOOPBACK} is already taken — {why} — so the daemon "
        f"did not start. If it is another `pm-ai daemon run`, that one is the "
        f"daemon; `pm-ai doctor` says whether it answers. Otherwise choose a "
        f"free port with `{_PORT_KEY}` in ~/.pm-ai/config.toml."
    )


def take_token(daemon: Daemon) -> str:
    """The daemon's token: read if present, minted if absent, never minted over.

    The create is exclusive, so two daemons that both found no file — one of
    which will lose the port race and never get here, but a hand-run copy
    could — cannot replace each other's token: the second create fails, and
    that process reads what the first wrote.
    """
    storage = daemon.storage
    try:
        token = storage.read_api_token()
        if token is not None:
            return token
        minted = secrets.token_urlsafe(TOKEN_BYTES)
        try:
            storage.create_api_token(minted)
        except FileExistsError:
            found = storage.read_api_token()
            if found is None:
                raise DaemonRefused(
                    "the API token file appeared and vanished while the daemon "
                    "was minting one, so there is no token to check requests "
                    "against. Nothing was started; run it again."
                ) from None
            return found
        return minted
    except ApiTokenRefused as unusable:
        raise DaemonRefused(f"the daemon did not start: {unusable}") from unusable
    except OSError as unreadable:
        where = getattr(unreadable, "filename", None) or str(storage.api_token_path())
        raise DaemonRefused(
            f"the daemon did not start: the API token file {where} could not be "
            f"read or written ({unreadable.strerror or unreadable}). Nothing was "
            f"changed; check its ownership and permissions."
        ) from unreadable


def status_body(
    report: Report, *, started_at: datetime, started: float
) -> Mapping[str, object]:
    """`GET /v1/status`'s body: every probe, the verdict, and how long it has run.

    The fields `4r` and `4q` build against: `probes` (each a `name`, `state` and
    `detail`), `healthy`, `uptime_seconds` and `started_at`. `uptime_seconds` is
    read off the monotonic clock, so a wall-clock change does not make the
    daemon younger; `started_at` is the daemon's own clock at start, in UTC.
    """
    return {
        "probes": [
            {"name": probe.name, "state": probe.health.value, "detail": probe.detail}
            for probe in report.probes
        ],
        "healthy": report.healthy,
        "uptime_seconds": int(time.monotonic() - started),
        "started_at": started_at.astimezone(timezone.utc).isoformat(),
    }


def attach_background(state: Any, requests: RequestPath, *, now: Callable[[], datetime]) -> None:
    """What `_lifespan` starts, set on the application's `state` by the serve path.

    Extracted from `_serve_locked` (story 15a) so a test drives the same
    wiring the daemon serves with.
    """
    state.pm_ai_requests = requests
    # The scheduled harvests (story 9a): attached in `_lifespan`, on this
    # loop; an unfinished one is marked by the shutdown's own step.
    state.pm_ai_timers = HarvestTimers(requests=requests, now=now)
    # The 07:00 render of every dashboard scope (story 9b), attached after
    # the harvests'; a render still before its claimed step is marked by
    # the shutdown's own step too.
    state.pm_ai_renders = RenderTimers(requests=requests, now=now)
    # Story 13a: the expiry sweep of staged proposals (AD-13), at start and
    # every 15 minutes after.
    state.pm_ai_sweep = ProposalSweep(requests=requests, now=now)
    # Story 15a: once at each start, the commitment lines approvals left
    # unwritten — items approved before 15a among them.
    state.pm_ai_commitments = CommitmentCatchUp(requests=requests, now=now)
    # Story 15b (temporary, until 10a): the commitment index, derived after
    # each append this daemon makes to a ledger, and checked once at start.
    indexing = CommitmentIndexing(requests=requests, now=now)
    indexing.attach()
    state.pm_ai_index = indexing


@contextlib.asynccontextmanager
async def _lifespan(app: Any) -> AsyncIterator[None]:
    """Where background work attaches when the application starts serving.

    The daily removal of old jobs (story 4r) and the scheduled harvests
    (story 9a) start here, on the daemon's one event loop, and are cancelled on
    the way out — after the listener has stopped and before the writer is
    closed. `9b`'s 07:00 render attaches here too, after the harvests.
    """
    requests: RequestPath | None = getattr(app.state, "pm_ai_requests", None)
    if requests is None:
        yield
        return
    requests.events.attach(asyncio.get_running_loop())
    timers: list[asyncio.Task[None]] = [
        asyncio.create_task(requests.prune_daily(), name="pm-ai-daily-prune")
    ]
    harvests: HarvestTimers | None = getattr(app.state, "pm_ai_timers", None)
    if harvests is not None:
        timers.append(asyncio.create_task(harvests.run(), name="pm-ai-harvests"))
    renders: RenderTimers | None = getattr(app.state, "pm_ai_renders", None)
    if renders is not None:
        timers.append(asyncio.create_task(renders.run(), name="pm-ai-morning-render"))
    sweep: ProposalSweep | None = getattr(app.state, "pm_ai_sweep", None)
    if sweep is not None:
        timers.append(asyncio.create_task(sweep.run(), name="pm-ai-proposal-sweep"))
    # Story 15a: a one-shot task, after the sweep; it never stops the start.
    commitments: CommitmentCatchUp | None = getattr(app.state, "pm_ai_commitments", None)
    catching_up: asyncio.Task[None] | None = None
    if commitments is not None:
        catching_up = asyncio.create_task(commitments.run(), name="pm-ai-commitment-catch-up")
        timers.append(catching_up)
    # Story 15b: the commitment index's task; its start check waits on the catch-up.
    indexing: CommitmentIndexing | None = getattr(app.state, "pm_ai_index", None)
    if indexing is not None:
        timers.append(
            asyncio.create_task(indexing.run(catching_up), name="pm-ai-commitment-index")
        )
    try:
        yield
    finally:
        if indexing is not None:
            # The hook first: an append from here on starts nothing.
            indexing.stop()
        for timer in timers:
            timer.cancel()
        for timer in timers:
            try:
                await timer
            except asyncio.CancelledError:
                pass
            except Exception as died:  # noqa: BLE001 — one dead timer stops no other
                _log.error(
                    "the background task %s had stopped on an unexpected %s",
                    timer.get_name(),
                    type(died).__name__,
                )


class _JsonLines(logging.Formatter):
    """AD-24's structured diagnostic line: one JSON object per record.

    `message` is the human-readable `GET /v1/status 401`; `method`, `path` and
    `status` are the same facts as fields. Nothing else from the request is in
    a record to be written — the API wrapper never passes a header or a query.
    """

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "logger": record.name,
            "level": record.levelname.lower(),
            "message": record.getMessage(),
        }
        for key in ("method", "path", "status", "event", "found", "job", "jobs", "step", "instance"):
            if hasattr(record, key):
                line[key] = getattr(record, key)
        return json.dumps(line, ensure_ascii=False)


class _TextlessQueueHandler(logging.handlers.QueueHandler):
    """The queue's feeder, keeping an exception's text out of the file (story 4r).

    `QueueHandler.prepare` folds `exc_info` — the exception's message included
    — into the line. An exception's text can carry a token from a provider, so
    the line keeps its type name and the frames it was raised through, never
    its message: the same rule a job's result follows.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        prepared = logging.makeLogRecord(record.__dict__)
        message = record.getMessage()
        if record.exc_info and record.exc_info[0] is not None:
            kind, _, tb = record.exc_info
            # Where it was raised, never what it said: file, line and function,
            # without the source line, which can hold a literal of its own.
            frames = "".join(
                f"  File {frame.filename}, line {frame.lineno}, in {frame.name}\n"
                for frame in traceback.extract_tb(tb)
            )
            message = f"{message}\n{kind.__name__} (its text is not logged)\n{frames}".rstrip()
        prepared.msg = message
        prepared.args = None
        prepared.exc_info = None
        prepared.exc_text = None
        prepared.stack_info = None
        return prepared


_LOGGERS = (REQUEST_LOGGER, DAEMON_LOGGER)
"""The request log and the daemon's own diagnostic lines: one file, one listener."""


class _RequestLog:
    """The request log's file, fed through a queue by one listener thread (4r).

    The loop never writes a log line: a request's line is put on a queue the
    loop never waits on, and one listener thread writes it to the rotating
    file (AD-19). The daemon's own diagnostic lines — a refused step, an
    overtaken job, a schema-version stop — go the same way to the same file.
    """

    def __init__(
        self,
        handler: logging.Handler,
        feeder: logging.Handler,
        listener: logging.handlers.QueueListener,
        saved: Mapping[str, tuple[bool, int]],
    ) -> None:
        self._handler = handler
        self._feeder = feeder
        self._listener = listener
        self._saved = saved

    @classmethod
    def attach(cls, daemon: Daemon) -> _RequestLog:
        """Open `~/.pm-ai/logs/daemon.log` and route the request log to it, or refuse."""
        try:
            handler = daemon.storage.diagnostic_log(REQUEST_LOG)
        except OSError as unopenable:
            where = getattr(unopenable, "filename", None) or f"~/.pm-ai/logs/{REQUEST_LOG}"
            raise DaemonRefused(
                f"the daemon did not start: its request log at {where} could not "
                f"be opened ({unopenable.strerror or unopenable}). Check that "
                f"~/.pm-ai/logs is a directory pm-ai can write to."
            ) from unopenable
        handler.setFormatter(_JsonLines())
        lines: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
        feeder = _TextlessQueueHandler(lines)
        listener = logging.handlers.QueueListener(lines, handler)
        listener.start()
        saved: dict[str, tuple[bool, int]] = {}
        for name in _LOGGERS:
            logger = logging.getLogger(name)
            saved[name] = (logger.propagate, logger.level)
            logger.setLevel(logging.INFO)
            # Not propagated: these lines belong in their file, and a root
            # handler configured by anything else in the process must not copy
            # them to a console.
            logger.propagate = False
            logger.addHandler(feeder)
        return cls(handler, feeder, listener, saved)

    def detach(self) -> None:
        """Write what is queued, remove the handler; each logger gets back what it had."""
        for name, (propagate, level) in self._saved.items():
            logger = logging.getLogger(name)
            logger.removeHandler(self._feeder)
            logger.propagate = propagate
            logger.setLevel(level)
        self._listener.stop()
        self._handler.close()


def _server_class(uvicorn: Any) -> Any:
    """uvicorn's `Server`, with pm-ai's stop signals and no re-raise afterwards.

    uvicorn handles `SIGINT` and `SIGTERM` and, once it has shut down, raises
    the captured signal again — which would turn a clean Ctrl-C into a
    `KeyboardInterrupt` out of `main` and an exit code of 3. The daemon also
    treats `SIGHUP`, the terminal closing, as a stop. So the capture is
    replaced: all three set uvicorn's own exit flag (a second Ctrl-C forces it,
    as uvicorn does), the previous handlers come back afterwards, and nothing
    is re-raised.
    """

    class _Server(uvicorn.Server):  # type: ignore[misc, name-defined]
        announce: Callable[[], None] | None = None
        on_stop: Callable[[], None] | None = None
        first_stop: int | None = None
        """The first stop request this server saw, named by the stop line (4s)."""

        async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
            server: Any = self
            if server.on_stop is not None:
                server.on_stop()
            await super().shutdown(sockets=sockets)

        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:
            if threading.current_thread() is not threading.main_thread():
                yield
                return
            previous = {sig: signal.signal(sig, self._stop) for sig in STOP_SIGNALS}
            try:
                yield
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)

        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            server: Any = self
            if server.started and server.announce is not None:
                server.announce()

        def _stop(self, sig: int, frame: FrameType | None) -> None:
            server: Any = self
            if server.first_stop is None:
                server.first_stop = sig
            if server.should_exit and sig == signal.SIGINT:
                server.force_exit = True
            else:
                server.should_exit = True

    return _Server

