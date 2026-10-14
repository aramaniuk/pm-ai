"""A line is answered at once, or acknowledged with a job (AD-21, AD-20, story 4r).

The daemon's request path. A client sends one text line — the words of a fixed
`pm-ai` command — and gets back either the command's output and exit code, or a
job id it reads the result from later. The request path is a transport: a
command's words sent here deliver what the same words deliver at a terminal.

## Which answer a line gets

- `help` — the command list, inline, exit 0.
- A first word that names no command — one sentence saying so and naming
  `help`, inline, exit 2.
- A command that needs a terminal of its own — it asks questions or takes a
  secret (`TERMINAL_BOUND`) — refused by name, inline, exit 3.
- Anything else is parsed exactly as the command line is, and the pipeline's
  declared bound decides (`pm_ai.core.dispatch.plan`): under five seconds it
  runs now, on the I/O pool, with its own output buffers; at five or over its
  row is written `queued` and the `202` names it.

## A job

The row is in `requests` before the `202` is sent. A worker moves it to
`running`, runs the command — bounded steps on the worker, every write handed to
the writer thread as a claimed step — and settles it `done` with the output and
exit code, or `failed` with pm-ai's own sentence and the error's type name,
never an exception's text. Every move is conditional on the state it expects, so
a job finishing after the shutdown marked it `failed` changes nothing and is
logged by name.

## Fresh reads per line

`config.toml`, the connector rows and the credentials are read again at the
start of every line and every job (AD-5); the enrolled projects are the ones the
daemon read at start, and a row filing into a project enrolled since is named
with the shared restart sentence instead of being probed.
"""

from __future__ import annotations

import asyncio
import functools
import io
import json
import logging
import shlex
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from pm_ai.app.concurrency import (
    WRITER_START_WAIT_SECONDS,
    IOPool,
    StepKind,
    WriterBusy,
    WriterStopped,
    WriterThread,
)
from pm_ai.app.pipelines import (
    ask_dashboard_exclusion,
    drop_transcript,
    prepare_dashboard,
    publish_dashboard,
)
from pm_ai.app.wiring import (
    handed_daemon,
    onboard_in_daemon,
    CLAIM_WAIT_ATTEMPTS,
    CLAIM_WAIT_INTERVAL,
    REGISTRY_ARTIFACT,
    Daemon,
    HandedRefreshTokenStore,
    refreshed,
)
from pm_ai.connectors.registry import ConnectorRegistry
from pm_ai.core.cadence import coverage_cutoff
from pm_ai.core.config import ConfigRefused
from pm_ai.core.dispatch import JobState, plan
from pm_ai.core.project_registry import RegistryRefused, parse_registry
from pm_ai.domain.health import Report
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import ArtifactBusy, DaemonRefused, KeyNotFound
from pm_ai.storage.service import (
    OperationalStoreStuck,
    OperationalStoreUnavailable,
    RequestRow,
    SchemaVersionChanged,
)
from pm_ai.surfaces.api.app import Reply
from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_UNEXPECTED,
    EXIT_USAGE,
    RESTART_SENTENCE,
    TAKES_A_PATH,
    TERMINAL_BOUND,
    Answered,
    HealthReport,
    OnboardOutcome,
    Parsed,
    dispatch,
    names_a_command,
    parse,
    usage,
)

__all__ = [
    "DAEMON_LOGGER",
    "JOB_RETENTION",
    "RequestPath",
]

DAEMON_LOG_NAME = "daemon.log"
"""The one file under `~/.pm-ai/logs/` the daemon writes (AD-24): the request
log, the daemon's own diagnostic lines, and what the restart pass reads."""

DAEMON_LOGGER = "pm_ai.daemon"
"""The daemon's own diagnostic lines: refused steps, overtaken jobs, a schema stop."""

JOB_RETENTION = timedelta(days=7)
"""How long a `requests` row is kept: removed at start and once a day after."""


WRITER_HALT_EVENT = "writer-halted"
"""The diagnostic log's mark for a writer halted on anything else."""

SCHEMA_MISMATCH_EVENT = "schema-version-mismatch"
"""The diagnostic log's mark for a schema-version stop, read by the restart pass."""

DEDUPLICATED = frozenset({("dashboard",), ("connector", "check"), ("doctor",)})
"""Commands where a second run while one is unfinished is the same job: they take
no arguments besides their scope. `project add` and `transcript drop` never are."""

STREAM_REREAD_SECONDS = 5.0
"""How long a stream waits for a published change before reading the row again."""


_HELP_WORDS = frozenset({"help", "-h", "--help"})
_ORDER = {state.value: index for index, state in enumerate(JobState)}
_STATE_ATTEMPTS = CLAIM_WAIT_ATTEMPTS
_STATE_INTERVAL = CLAIM_WAIT_INTERVAL

_log = logging.getLogger(DAEMON_LOGGER)


def _escaped(job_id: str, finished: Any) -> None:
    """Log what a job's worker let escape — its type, never its text."""
    if finished.cancelled():
        return
    error = finished.exception()
    if error is not None:
        _log.error(
            "the job %s's worker stopped on an unexpected %s",
            job_id, type(error).__name__, extra={"job": job_id},
        )


def row_body(row: RequestRow) -> dict[str, object]:
    """A row as `GET /v1/jobs/<id>` answers it: `result` decoded from its JSON."""
    result: object = None
    if row.result is not None:
        try:
            result = json.loads(row.result)
        except ValueError:
            result = row.result
    return {
        "id": row.id,
        "kind": row.kind,
        "scope": row.scope,
        "state": row.state,
        "result": result,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _already_serving() -> int:
    """`pm-ai daemon run` sent through the daemon: this one is already running (4q)."""
    raise DaemonRefused(
        "a pm-ai daemon is already running — this one, which answered the line — "
        "so `pm-ai daemon run` started nothing. To start it again, stop it first "
        "(Ctrl-C in its terminal)."
    )


def _inline(output: str, code: int) -> Reply:
    return Reply(200, {"inline": True, "output": output, "exit": code})


def _busy(detail: str) -> Reply:
    return Reply(503, {"detail": detail}, {"Retry-After": "1"})


def _unexpected(error: BaseException, *, doing: str) -> str:
    """pm-ai's own sentence for an error it did not expect: the type, never the text."""
    return (
        f"pm-ai stopped {doing} on an unexpected {type(error).__name__}; that is "
        f"a bug in pm-ai. The error's own text is not repeated here, because "
        f"text from a provider can carry a token."
    )


@dataclass
class JobNotes:
    """What each running job last finished — for the sentence a stop leaves, never state.

    The row is the job (AD-20); this only remembers which claimed step each
    job finished last, so a job stopped mid-way is marked `failed` naming it.
    """

    _last: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def finished(self, job: str | None, label: str) -> None:
        if job is None:
            return
        with self._lock:
            self._last[job] = label

    def last(self, job: str) -> str | None:
        with self._lock:
            return self._last.get(job)

    def forget(self, job: str) -> None:
        with self._lock:
            self._last.pop(job, None)


class JobEvents:
    """Hands each job's state changes to the streams reading it, on the loop."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._readers: dict[str, set[asyncio.Queue[RequestRow | None]]] = {}
        self._lock = threading.Lock()
        self._closed = False

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, job_id: str) -> asyncio.Queue[RequestRow | None]:
        queue: asyncio.Queue[RequestRow | None] = asyncio.Queue()
        with self._lock:
            if self._closed:
                # Opened after the stop began: it ends after its first event.
                queue.put_nowait(None)
            self._readers.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[RequestRow | None]) -> None:
        with self._lock:
            readers = self._readers.get(job_id)
            if readers is not None:
                readers.discard(queue)
                if not readers:
                    del self._readers[job_id]

    def publish(self, row: RequestRow) -> None:
        """From any thread: every stream on this job gets the row."""
        self._send(row.id, row)

    def close_all(self) -> None:
        """The daemon is stopping: every open stream ends, so the listener can close."""
        with self._lock:
            self._closed = True
            jobs = list(self._readers)
        for job in jobs:
            self._send(job, None)

    def _send(self, job_id: str, item: RequestRow | None) -> None:
        loop = self._loop
        with self._lock:
            readers = list(self._readers.get(job_id, ()))
        if loop is None or not readers:
            return
        for queue in readers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                return  # the loop has closed; nobody is reading


@dataclass
class RequestPath:
    """The daemon's request path: lines, jobs and their streams (story 4r).

    `diagnose` is `doctor`'s report, bound by the composition root to the
    keychain this process holds.
    `_slots` holds one lock per connector instance: the one refresh of its
    credential that may be in flight in the daemon (AD-5), shared by every
    store built for that instance.
    """

    daemon: Daemon
    writer: WriterThread
    pool: IOPool
    diagnose: Callable[[], HealthReport]
    bounds: Mapping[tuple[str, ...], Callable[[Daemon], float]] = field(default_factory=dict)
    """Every command's declared bound, by its words (AD-21); see `entry.DAEMON_BOUNDS`."""
    events: JobEvents = field(default_factory=JobEvents)
    wait_a_day: Callable[[float], Awaitable[None]] = asyncio.sleep
    """The daily prune's wait; a test hands one that does not take a day."""
    notes: JobNotes = field(default_factory=JobNotes)
    shutdown_marks: list[Callable[[], object]] = field(default_factory=list)
    """More of the shutdown's own work, each run as its own shutdown step on the
    writer thread after the jobs are marked: `9a`'s mark of a harvest still in
    a fetch."""
    _slots: dict[str, threading.Lock] = field(default_factory=dict)
    _slots_lock: threading.Lock = field(default_factory=threading.Lock)

    # ── the endpoints ────────────────────────────────────────────────────────

    async def prompt(self, line: str) -> Reply:
        """`POST /v1/prompt`: answer the line inline, or with a job id."""
        plain = line.split()
        if not plain or not names_a_command(plain[0]):
            first = plain[0] if plain else line
            return _inline(
                f"pm-ai: {first!r} is not a pm-ai command; `help` lists the commands.\n",
                EXIT_USAGE,
            )
        try:
            words = shlex.split(line)
        except ValueError as unreadable:
            return _inline(
                f"pm-ai: the line could not be read as words ({unreadable}); "
                f"`help` lists the commands.\n",
                EXIT_USAGE,
            )
        if words[0] in _HELP_WORDS and len(words) == 1:
            return _inline(usage() + "\n", EXIT_OK)
        needs = next(
            (spelled for spelled in TERMINAL_BOUND if tuple(words[: len(spelled)]) == spelled),
            None,
        )
        if needs is not None:
            named = " ".join(needs)
            return _inline(
                f"pm-ai: `pm-ai {named}` asks questions or takes a secret, so it "
                f"needs a terminal of its own and is not run through the daemon. "
                f"Run `pm-ai {named}` at a terminal.\n",
                EXIT_REFUSAL,
            )
        parsed = parse(words)
        if isinstance(parsed, Answered):
            return _inline(parsed.text + "\n", parsed.code)
        if tuple(words[:2]) in TAKES_A_PATH and parsed.arguments:
            given = parsed.arguments[0]
            if not Path(given).expanduser().is_absolute():
                # The daemon's working directory is not the client's, so a
                # relative path would name a different place there.
                return _inline(
                    f"pm-ai: {given!r} is a relative path, and the daemon does not "
                    f"run in the directory it was typed in; give `pm-ai "
                    f"{' '.join(words[:2])}` an absolute path.\n",
                    EXIT_USAGE,
                )
        # The line's `--scope`, resolved for this request against the projects
        # the daemon watches (story 4q): a project it does not watch, or one
        # enrolled since it started, is refused in the answer, by name.
        unwatched = await self._unwatched(parsed.target)
        if unwatched is not None:
            return _inline(f"pm-ai: {unwatched}\n", EXIT_REFUSAL)
        declared = self._bound(words)
        if declared is None:
            # Never a guess at the call site: a command without a declared
            # bound is a pm-ai bug, and the test over `DAEMON_BOUNDS` says so.
            return _inline(
                f"pm-ai: `pm-ai {' '.join(words[:2])}` declares no bound, so the "
                f"daemon cannot say whether to answer at once or acknowledge it. "
                f"That is a bug in pm-ai; run it at a terminal.\n",
                EXIT_UNEXPECTED,
            )
        kind, bound = declared
        decided = plan(estimated_seconds=bound)
        if decided.inline or decided.job_id is None:
            output, code = await self.pool.run(lambda: self._inline_command(parsed))
            return _inline(output, code)
        scope = "application" if parsed.target is None else str(parsed.target)
        # Recording the row and handing it to a worker are one unit, shielded
        # from the request's own cancellation: a client that leaves after the
        # row is written must not leave a `queued` row no worker will run —
        # the dedup would hand that dead id to every later request.
        return await asyncio.shield(
            asyncio.ensure_future(
                self._record_and_start(
                    decided.job_id,
                    kind,
                    scope,
                    parsed,
                    deduplicate=tuple(words[:2]) in DEDUPLICATED
                    or tuple(words[:1]) in DEDUPLICATED,
                )
            )
        )

    async def _unwatched(self, target: DataScope | None) -> str | None:
        """Why the line cannot act in its project here, or `None` when it can.

        Story 4m's order without the folder step — the daemon runs in no folder
        of the person's: a project named on the line, which must be one the
        daemon watches. No command's target defaults to a project, so there is
        no unnamed case to resolve. The projects are the ones read at start
        (AD-5, AD-7); the registry is read now, as a plain read on a worker,
        only to tell a project enrolled since the start from one never
        enrolled.
        """
        if target is None or target.kind is not ScopeKind.PROJECT:
            return None
        named = target.project_id
        watched = self.daemon.watched
        if named in watched:
            return None
        try:
            enrolled = await self.pool.run(self._read_registry)
        except (RegistryRefused, OSError, ValueError) as unreadable:
            return (
                f"{target} is not a project this daemon watches, and the project "
                f"registry could not be read ({type(unreadable).__name__}: "
                f"{unreadable}), so whether it was enrolled since the daemon "
                f"started cannot be told; the line was not run. `pm-ai doctor` "
                f"reports the registry."
            )
        if named in enrolled:
            return (
                f"{target} was enrolled after this daemon started, so it does "
                f"not act in it yet. {RESTART_SENTENCE}"
            )
        listed = ", ".join(sorted(watched)) if watched else "no project"
        return (
            f"{target} is not a project this daemon watches, so the line was not "
            f"run; it watches {listed}. Name one of them with "
            f"`--scope project:<id>`, or enrol {named} first with "
            f"`pm-ai project add <path> {named}`. {RESTART_SENTENCE}"
        )

    async def _record_and_start(
        self, job_id: str, kind: str, scope: str, parsed: Parsed, *, deduplicate: bool
    ) -> Reply:
        try:
            row, created = await self.writer.call_async(
                lambda: self.daemon.storage.create_request(
                    job_id=job_id, kind=kind, scope=scope, deduplicate=deduplicate
                ),
                label=f"record the {kind} job",
                job=job_id,
                start_within=WRITER_START_WAIT_SECONDS,
            )
        except WriterBusy as busy:
            return _busy(str(busy))
        except WriterStopped:
            return _busy(f"the daemon is stopping, so the {kind} job was not recorded.")
        except (SchemaVersionChanged, OperationalStoreStuck) as stopped:
            return _busy(
                f"the daemon stopped, so the {kind} job was not recorded: {stopped}"
            )
        except (ArtifactBusy, OperationalStoreUnavailable) as refused:
            return _busy(
                f"the {kind} job could not be recorded, so it was not started: "
                f"{refused} Try again in a second."
            )
        if created:
            self.notes.finished(row.id, f"record the {kind} job")
            self.events.publish(row)
            try:
                started = self.pool.submit(lambda: self._run_job(row.id, parsed, kind))
            except RuntimeError:
                # The pool stopped taking work: the daemon is stopping. The row
                # it would have run is settled now, by the conditional move.
                await self.pool_closed(row.id)
                return _busy(f"the daemon is stopping, so the {kind} job was not started.")
            started.add_done_callback(functools.partial(_escaped, row.id))
        return Reply(202, {"inline": False, "job_id": row.id})

    async def pool_closed(self, job_id: str) -> None:
        """Settle a `queued` row no worker will run, so its dedup slot is freed."""
        result = json.dumps(
            {"error": "the daemon stopped before this job started", "type": "DaemonStopped"}
        )
        try:
            settled = await self.writer.call_async(
                lambda: self.daemon.storage.move_request(
                    job_id, expected=JobState.QUEUED.value, to=JobState.FAILED.value, result=result
                ),
                label="mark a job no worker took failed",
                kind=StepKind.SHUTDOWN,
                job=job_id,
            )
            if settled is not None:
                self.events.publish(settled)
        except Exception as unsettled:  # noqa: BLE001 — the next start's pass settles it
            _log.warning(
                "the job %s could not be marked failed (%s); the next start will",
                job_id,
                type(unsettled).__name__,
                extra={"job": job_id},
            )
        self.notes.forget(job_id)

    async def job(self, job_id: str) -> Mapping[str, object] | None:
        """`GET /v1/jobs/<id>`: the row, read on a worker; `None` when unknown."""
        row = await self.pool.run(lambda: self.daemon.storage.read_request(job_id))
        return None if row is None else row_body(row)

    async def job_events(self, job_id: str) -> AsyncIterator[Mapping[str, object]] | None:
        """`GET /v1/jobs/<id>/events`: each state change, then close; `None` when unknown.

        Nothing is subscribed until the stream is read: a client that leaves
        before its first event holds no subscription.
        """
        row = await self.pool.run(lambda: self.daemon.storage.read_request(job_id))
        if row is None:
            return None
        return self._stream(job_id)

    async def _stream(self, job_id: str) -> AsyncIterator[Mapping[str, object]]:
        queue = self.events.subscribe(job_id)
        try:
            # Read after subscribing, so no change between the two is missed.
            first = await self.pool.run(lambda: self.daemon.storage.read_request(job_id))
            if first is None:
                return
            last = first.state
            yield row_body(first)
            while not JobState(last).finished:
                try:
                    update = await asyncio.wait_for(queue.get(), STREAM_REREAD_SECONDS)
                except TimeoutError:
                    # Nothing published: read the row again, so a stream never
                    # waits on an event that was lost.
                    update = await self.pool.run(
                        lambda: self.daemon.storage.read_request(job_id)
                    )
                    if update is None:
                        return
                if update is None:
                    return  # the daemon is stopping; a reader reconnects for the end
                if _ORDER.get(update.state, -1) <= _ORDER.get(last, -1):
                    continue
                last = update.state
                yield row_body(update)
        finally:
            self.events.unsubscribe(job_id, queue)

    async def status(self, probes: Callable[[], Mapping[str, object]]) -> Mapping[str, object]:
        """`GET /v1/status`, its probes on the I/O pool: the loop only awaits."""
        return await self.pool.run(probes)

    # ── start, the daily prune, and stop ─────────────────────────────────────

    def restart_pass(self) -> tuple[str, ...]:
        """At start: every row an earlier run left unfinished becomes `failed`.

        Nothing is re-run (story 10's). The reason names a schema-version stop
        when the diagnostic log holds that line for the job.
        """
        stopped_on_mismatch = self._mismatched_jobs()

        def reason(row: RequestRow) -> str:
            if row.id in stopped_on_mismatch:
                sentence = (
                    "the daemon stopped on a schema-version mismatch; see the "
                    "diagnostic log"
                )
            else:
                sentence = "the daemon stopped before this job finished"
            return json.dumps({"error": sentence, "type": "DaemonStopped"})

        settled = self.writer.call(
            lambda: self.daemon.storage.fail_unfinished_requests(reason),
            label="mark jobs an earlier run left unfinished failed",
        )
        for row in settled:
            self.events.publish(row)
        return tuple(row.id for row in settled)

    def prune(self) -> int:
        """Remove rows older than seven days; how many went."""
        return self.writer.call(
            lambda: self.daemon.storage.prune_requests(older_than=JOB_RETENTION),
            label="remove jobs older than seven days",
        )

    def prune_coverage(self) -> int:
        """Remove coverage records older than 90 days (story 9a); how many went.

        At start and once a day after. Never refuses anything: a prune that
        does not run is logged by type and tried again tomorrow.
        """
        try:
            return self.writer.call(
                lambda: self.daemon.storage.prune_coverage(
                    before=coverage_cutoff(self.daemon.clock())
                ),
                label="remove old coverage records",
            )
        except Exception as failed:  # noqa: BLE001 — tomorrow tries again
            _log.warning(
                "the removal of old coverage records did not run: %s",
                type(failed).__name__,
            )
            return 0

    async def prune_daily(self) -> None:
        """Once a day after start, until the daemon stops and cancels it."""
        while True:
            await self.wait_a_day(timedelta(days=1).total_seconds())
            try:
                await self.writer.call_async(
                    lambda: self.daemon.storage.prune_requests(older_than=JOB_RETENTION),
                    label="remove jobs older than seven days",
                )
            except Exception as failed:  # noqa: BLE001 — tomorrow tries again
                _log.warning(
                    "the daily removal of old jobs did not run: %s",
                    type(failed).__name__,
                )
            await self.pool.run(self.prune_coverage)

    def shutdown_pass(self) -> tuple[str, ...]:
        """The shutdown's own step: jobs still unfinished are marked `failed`.

        Runs after every claimed step already queued, so a job whose step was
        queued finishes it first; a job still in a bounded step is marked here,
        naming the last claimed step it finished.
        """

        def reason(row: RequestRow) -> str:
            if row.state == JobState.QUEUED.value:
                sentence = "the daemon stopped before this job started"
            else:
                last = self.notes.last(row.id)
                sentence = (
                    "the daemon stopped before this job finished"
                    + (f"; the last step it finished was {last!r}" if last else "")
                )
            return json.dumps({"error": sentence, "type": "DaemonStopped"})

        try:
            settled = self.writer.call(
                lambda: self.daemon.storage.fail_unfinished_requests(reason),
                label="mark unfinished jobs failed",
                kind=StepKind.SHUTDOWN,
            )
        finally:
            for mark in self.shutdown_marks:
                try:
                    self.writer.call(
                        mark, label="mark an unfinished harvest failed", kind=StepKind.SHUTDOWN
                    )
                except Exception as unmarked:  # noqa: BLE001 — named; the stop goes on
                    _log.warning(
                        "the shutdown could not mark an unfinished harvest failed (%s)",
                        type(unmarked).__name__,
                    )
        for row in settled:
            self.events.publish(row)
        return tuple(row.id for row in settled)

    def _mismatched_jobs(self) -> frozenset[str]:
        named: set[str] = set()
        for line in self.daemon.storage.diagnostic_lines(DAEMON_LOG_NAME):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("event") == SCHEMA_MISMATCH_EVENT:
                job = record.get("job")
                if isinstance(job, str):
                    named.add(job)
                jobs = record.get("jobs")
                if isinstance(jobs, list):
                    named.update(item for item in jobs if isinstance(item, str))
        return frozenset(named)

    # ── running a command ────────────────────────────────────────────────────

    def _bound(self, words: Sequence[str]) -> tuple[str, float] | None:
        """The kind of job a line would be, and its command's declared bound.

        `None` when nothing declares one — never a guess.
        """
        for length in (2, 1):
            spelled = tuple(words[:length])
            declared = self.bounds.get(spelled) if len(spelled) == length else None
            if declared is not None:
                return " ".join(spelled), declared(self.daemon)
        return None

    def _inline_command(self, parsed: Parsed) -> tuple[str, int]:
        try:
            return self._command(parsed, job=None)
        except (WriterStopped, SchemaVersionChanged) as stopped:
            return f"pm-ai: {stopped}\n", EXIT_REFUSAL
        except Exception as error:  # noqa: BLE001 — answered, as a terminal's exit 1
            _log.warning("a line stopped on an unexpected %s", type(error).__name__)
            return f"pm-ai: {_unexpected(error, doing='this command')}\n", EXIT_UNEXPECTED

    def _run_job(self, job_id: str, parsed: Parsed, kind: str) -> None:
        """On a worker: `queued` → `running` → `done` or `failed`, each a claimed step."""
        try:
            self._job_body(job_id, parsed, kind)
        finally:
            self.notes.forget(job_id)

    def _job_body(self, job_id: str, parsed: Parsed, kind: str) -> None:
        if not self._move(job_id, JobState.QUEUED, JobState.RUNNING, label="start the job"):
            return
        try:
            output, code = self._command(parsed, job=job_id)
        except (WriterStopped, SchemaVersionChanged):
            # Refused and logged by name by the writer; the row is left for
            # the shutdown pass, or a schema stop's restart pass.
            return
        except BaseException as error:  # noqa: BLE001 — settled as `failed`
            _log.warning(
                "the %s job %s stopped on an unexpected %s",
                kind,
                job_id,
                type(error).__name__,
                extra={"job": job_id},
            )
            if isinstance(error, (ArtifactBusy, OperationalStoreUnavailable, KeyNotFound)):
                # A claim held, or the store busy past the daemon's wait: this
                # attempt fails with pm-ai's own sentence, which names the claim
                # — never silently (AD-5). The retry ladder is `9a`'s. No key
                # enrolled at start is pm-ai's own sentence naming
                # `pm-ai key enrol`, never "a bug".
                sentence = str(error)
            else:
                sentence = _unexpected(error, doing=f"the {kind} job")
            result = {"error": sentence, "type": type(error).__name__}
            self._move(
                job_id, JobState.RUNNING, JobState.FAILED,
                label="settle the job", result=result, final=True,
            )
            return
        self._move(
            job_id,
            JobState.RUNNING,
            JobState.DONE,
            label="settle the job",
            result={"output": output, "exit": code},
            final=True,
        )

    def _move(
        self,
        job_id: str,
        expected: JobState,
        to: JobState,
        *,
        label: str,
        result: Mapping[str, object] | None = None,
        final: bool = False,
    ) -> bool:
        """One conditional move, handed to the writer; the row it wrote is published.

        Busy past the daemon's 100 ms wait is handed over again by this worker
        after the interval, never by a sleep on the writer. A `final` move — to
        `done` or `failed` — never gives up while the writer is open: a row left
        `running` would hold its dedup slot and every open stream until the
        next start. Anything unexpected is logged (type only) and retried.
        """
        encoded = None if result is None else json.dumps(result, ensure_ascii=False)
        attempt = 0
        while True:
            attempt += 1
            try:
                moved = self.writer.call(
                    lambda: self.daemon.storage.move_request(
                        job_id, expected=expected.value, to=to.value, result=encoded
                    ),
                    label=label,
                    job=job_id,
                )
                break
            except (WriterStopped, SchemaVersionChanged):
                return False
            except Exception as error:  # noqa: BLE001 — busy, or unexpected: retried
                busy = isinstance(error, (ArtifactBusy, OperationalStoreUnavailable))
                if not busy:
                    _log.warning(
                        "the claimed step %r for job %s raised %s; it is handed over again",
                        label, job_id, type(error).__name__,
                        extra={"job": job_id, "step": label},
                    )
                if not final and attempt >= _STATE_ATTEMPTS:
                    _log.warning(
                        "the claimed step %r for job %s stayed busy and was given up",
                        label, job_id, extra={"job": job_id, "step": label},
                    )
                    return False
                time.sleep(_STATE_INTERVAL if busy else min(1.0, _STATE_INTERVAL * attempt))
        if moved is None:
            _log.warning(
                "the claimed step %r for job %s changed nothing: the job was no "
                "longer %s, so another step got there first",
                label,
                job_id,
                expected.value,
                extra={"job": job_id, "step": label},
            )
            return False
        self.notes.finished(job_id, label)
        self.events.publish(moved)
        return True

    def _command(self, parsed: Parsed, *, job: str | None) -> tuple[str, int]:
        """Run one parsed command with this request's own buffers; its output and code."""
        out, err = io.StringIO(), io.StringIO()
        try:
            fresh = self._fresh(job)
        except ConfigRefused as refused:
            return f"pm-ai: {refused}\n", EXIT_REFUSAL
        hand = self._hand_for(job)
        code = dispatch(
            parsed,
            daemon=fresh,
            diagnose=self.diagnose,
            probe_connectors=lambda: self._probe(fresh),
            onboard=lambda path, alias: onboard_in_daemon(fresh, path, alias, hand=hand),
            dashboard=lambda scope: self._dashboard(fresh, scope, job=job),
            # Runs here, on the worker: its reads, binding, `git` question and
            # extraction are bounded steps; each write is handed over.
            transcript_drop=functools.partial(drop_transcript, handed_daemon(fresh, hand=hand)),
            serve=_already_serving,
            unbuilt_connectors=fresh.unbuilt,
            out=out,
            err=err,
            terminal=False,
        )
        return out.getvalue() + err.getvalue(), code

    def fresh(self) -> Daemon:
        """The daemon as a scheduled job sees it, read again now (AD-5, story 9a).

        `config.toml`, the connector list, each row and the credentials — the
        same refresh a line gets, with the daemon's credential stores and their
        one-refresh-per-instance slots.
        """
        return self._fresh(None)

    def _fresh(self, job: str | None = None) -> Daemon:
        """`refreshed`, with the daemon's credential stores (and their refresh slots)."""
        return refreshed(
            self.daemon,
            store_for=lambda instance, system: self._store_for(instance, system, job=job),
            enrolled_now=self._enrolled_now(),
        )

    def _read_registry(self) -> tuple[str, ...]:
        """The registry as it is now: a plain read, never composed from; raises when unreadable."""
        raw = self.daemon.storage.read_artifact(
            scope=DataScope(ScopeKind.APPLICATION), artifact=REGISTRY_ARTIFACT
        )
        return tuple(parse_registry(raw)) if raw is not None else ()

    def _enrolled_now(self) -> tuple[str, ...]:
        """`_read_registry`, with an unreadable registry read as naming nothing new.

        For the connector refresh only, which names rows filing into later
        projects; a line's scope is resolved through `_read_registry` itself, so
        an unreadable registry is said rather than read as "never enrolled".
        """
        try:
            return self._read_registry()
        except (RegistryRefused, OSError, ValueError):
            return ()

    def _store_for(
        self, instance: str, system: str, *, job: str | None = None
    ) -> HandedRefreshTokenStore:
        with self._slots_lock:
            slot = self._slots.setdefault(instance, threading.Lock())
        return HandedRefreshTokenStore(
            storage=self.daemon.storage,
            instance=instance,
            system=system,
            hand=lambda work, label, save: self._hand_credential(work, label, save, job=job),
            log=_log,
            refresh_slot=slot,
        )

    def _hand_credential(
        self, work: Callable[[], Any], label: str, save: bool, *, job: str | None = None
    ) -> Any:
        """A credential's read or compare-and-save, noted as its job's step like any other."""
        result = self.writer.call(
            work,
            label=label,
            kind=StepKind.CREDENTIAL_SAVE if save else StepKind.ORDINARY,
            job=job,
        )
        self.notes.finished(job, label)
        return result

    def _probe(self, fresh: Daemon) -> Report:
        enumerated = ConnectorRegistry()
        for instance, connector in fresh.connectors.items():
            enumerated.register(connector, instance=instance)
        return enumerated.check_health()

    def _dashboard(self, fresh: Daemon, scope: DataScope, *, job: str | None) -> Path:
        """The render as steps: reads and render bounded, git bounded, the save claimed.

        A project scope here is one the daemon watches: `prompt` refused every
        other before the line was planned (story 4q).
        """
        document = prepare_dashboard(fresh, scope=scope, now=fresh.clock())
        ask_dashboard_exclusion(fresh, scope=scope)
        written: Path = self._hand_for(job)(
            lambda: publish_dashboard(fresh, scope=scope, document=document),
            f"save the {scope} dashboard",
        )
        return written

    def _hand_for(self, job: str | None) -> Callable[[Callable[[], Any], str], Any]:
        """The writer's door for one command: each step named, and noted for its job."""

        def hand(work: Callable[[], Any], label: str) -> Any:
            result = self.writer.call(work, label=label, job=job)
            self.notes.finished(job, label)
            return result

        return hand


