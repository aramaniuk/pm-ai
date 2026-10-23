"""A line is answered at once, or acknowledged with a job (AD-21, AD-20, story 4r).

The daemon's request path. A client sends one text line — the words of a fixed
`pm-ai` command — and gets back either the command's output and exit code, or a
job id it reads the result from later. The request path is a transport: a
command's words sent here deliver what the same words deliver at a terminal.

## Which answer a line gets

- `help` — the command list, inline, exit 0.
- A first word that names no command — one sentence saying so and naming
  `help`, inline, exit 2.
- A command that needs a terminal of its own — it asks questions, takes a
  secret, or holds the terminal until Ctrl-C (`TERMINAL_BOUND`) — refused by
  name, inline, exit 3.
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
from datetime import date, timedelta
from pathlib import Path
from typing import Any, TypeVar

from pm_ai.app.concurrency import (
    WRITER_START_WAIT_SECONDS,
    IOPool,
    ModelPool,
    StepKind,
    WriterBusy,
    WriterStopped,
    WriterThread,
)
from pm_ai.app.goals import GoalReader
from pm_ai.app.members import MemberDesk
from pm_ai.app.meeting_listing import MeetingLister
from pm_ai.app.proposals import ProposalDesk
from pm_ai.platform.paths import ScopePaths
from pm_ai.domain.meeting_summaries import SummaryRefused
from pm_ai.app.pipelines import (
    CALENDAR_FETCH_BUDGET_SECONDS,
    FetchAbandoned,
    ReadBound,
    ask_dashboard_exclusion,
    assert_dashboard_scope,
    drop_transcript,
    prepare_dashboard,
    publish_dashboard,
    record_failed_render,
    render_unrecorded,
    SummaryOutcome,
    choose_meeting,
    request_summary,
    summary_closing,
)
from pm_ai.connectors.registry import run_bounded
from pm_ai.domain.renders import RenderUnrecorded
from pm_ai.app.wiring import (
    handed_daemon,
    onboard_in_daemon,
    CLAIM_WAIT_ATTEMPTS,
    CLAIM_WAIT_INTERVAL,
    REGISTRY_ARTIFACT,
    Daemon,
    FrontierKeyCheck,
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
from pm_ai.core.frontier_key import assert_sealable, seal_frontier_key
from pm_ai.storage.crypto import PlaintextCrypto
from pm_ai.ports import ArtifactBusy, DaemonRefused, FrontierKeyRefused, KeyNotFound
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
    SUMMARIZE_WORDS,
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

_T = TypeVar("_T")

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


@dataclass(eq=False)
class _Rendering:
    """One render in flight: where it is, for the shutdown's own step (9b)."""

    scope: DataScope
    claimed: bool = False
    """Its claimed step has run (or failed) on the writer: nothing to mark."""
    in_fetch: bool = False
    started: bool = False
    """It holds its scope's render slot; before that it waits behind another."""
    finished: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class _PendingSummary:
    """A summary job's row, settled once both halves are in (story 7b).

    The command's own output arrives when `dispatch` returns; the summary's
    outcome when its last stage ends, on another thread, possibly first.
    """

    def __init__(self, settle: Callable[[str, int], None]) -> None:
        self._settle = settle
        self._lock = threading.Lock()
        self._started = False
        self._output: tuple[str, int] | None = None
        self._outcome: SummaryOutcome | BaseException | None = None
        self._arrived = False
        self._done = False

    @property
    def has_started(self) -> bool:
        with self._lock:
            return self._started or self._arrived

    def started(self) -> None:
        with self._lock:
            self._started = True

    def finished(self, outcome: SummaryOutcome | BaseException) -> None:
        with self._lock:
            self._outcome, self._arrived = outcome, True
        self._maybe()

    def returned(self, output: str, code: int) -> None:
        with self._lock:
            self._output = (output, code)
        self._maybe()

    def _maybe(self) -> None:
        with self._lock:
            if self._done or self._output is None:
                return
            if self._started and not self._arrived:
                return  # the summary is still running; its end settles the row
            self._done = True
            output, code = self._output
            outcome = self._outcome
        if outcome is not None:
            line, ended = summary_closing(outcome)
            output, code = output + line, max(code, ended)
        self._settle(output, code)


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
    render_budget: float = CALENDAR_FETCH_BUDGET_SECONDS
    """The per-connector budget each render's calendar read is held to (9b)."""
    _render_slots: dict[DataScope, threading.Lock] = field(default_factory=dict)
    """One lock per dashboard scope: at most one render of a scope in flight in
    the daemon, the 07:00 timer's and a dashboard job's together (story 9b)."""
    _render_slots_lock: threading.Lock = field(default_factory=threading.Lock)
    _renders: set[_Rendering] = field(default_factory=set)
    """The renders in flight, for the shutdown's own step."""
    key_check: FrontierKeyCheck | None = None
    """The frontier adapter's key check, for `pm-ai frontier key` (story 7c)."""
    key_pool: ModelPool | None = None
    """The frontier pool the key check runs on, so the shutdown's abort covers it (7c)."""

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
                f"pm-ai: `pm-ai {named}` asks questions, takes a secret or holds the "
                f"terminal until Ctrl-C, so it needs a terminal of its own and is not "
                f"run through the daemon. "
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

    async def frontier_key(self, key: str) -> Reply:
        """`POST /v1/frontier-key`: check the key with Anthropic, then seal it (story 7c).

        The check — listing models with the key, which carries no prompt and
        writes no ledger line — runs on an I/O worker through the frontier
        adapter's own client, so every request to Anthropic leaves from the
        daemon. Only a key Anthropic accepted is sealed, in one claimed step on
        the writer thread, under the master key; a refused key, or Anthropic
        not answering, seals nothing and a key already held stays. Answered
        inline, as a line is; the key is never in an answer or a log line.
        """
        if not key.strip():
            return _inline("pm-ai: no key was given, so nothing was sealed.\n", EXIT_REFUSAL)
        checker = self.key_check
        if checker is None:
            return _inline(
                "pm-ai: this daemon cannot check a key: the frontier adapter needs the "
                "runtime extra (`uv sync --extra runtime`). Nothing was sealed.\n",
                EXIT_REFUSAL,
            )
        storage = self.daemon.storage
        try:
            # Locally first, contacting nothing: a key with nowhere to be sealed
            # is never sent to Anthropic.
            await self.pool.run(lambda: assert_sealable(storage))
        except Exception as unsealable:  # noqa: BLE001 — named by type and sentence
            return _inline(
                f"pm-ai: a key could not be sealed here, so none was asked of Anthropic: "
                f"{type(unsealable).__name__}: {unsealable}\n",
                EXIT_REFUSAL,
            )
        pool = self.key_pool
        if pool is None:
            return _inline(
                "pm-ai: this daemon has no frontier pool to check a key on. That is a "
                "wiring fault in pm-ai; nothing was sealed.\n",
                EXIT_UNEXPECTED,
            )
        try:
            await pool.run(lambda: checker.check_key(key))
        except FrontierKeyRefused as refused:
            return _inline(f"pm-ai: {refused}\n", EXIT_REFUSAL)
        except Exception as error:  # noqa: BLE001 — named by type, never by text
            return _inline(
                f"pm-ai: {_unexpected(error, doing='checking the key')} Nothing was sealed.\n",
                EXIT_UNEXPECTED,
            )
        try:
            await self.writer.call_async(
                lambda: seal_frontier_key(storage, key),
                label="seal the frontier key",
                start_within=WRITER_START_WAIT_SECONDS,
            )
        except WriterBusy as busy:
            return _busy(str(busy))
        except (WriterStopped, SchemaVersionChanged) as stopped:
            return _inline(f"pm-ai: the key was checked and not sealed: {stopped}\n", EXIT_REFUSAL)
        except (ArtifactBusy, KeyNotFound, OperationalStoreUnavailable) as refused:
            return _inline(f"pm-ai: the key was checked and not sealed: {refused}\n", EXIT_REFUSAL)
        except Exception as error:  # noqa: BLE001 — named by type, never by text
            return _inline(
                f"pm-ai: {_unexpected(error, doing='sealing the key')} Nothing was sealed.\n",
                EXIT_UNEXPECTED,
            )
        how = (
            "stored UNENCRYPTED (PM_AI_DISABLE_ENCRYPTION is set for this daemon) in"
            if isinstance(self.daemon.crypto, PlaintextCrypto)
            else "sealed in"
        )
        return _inline(
            f"the Anthropic API key was checked with Anthropic and {how} "
            f"~/.pm-ai/private/config.json; `pm-ai transcript summarize --by claude` "
            f"uses it from now on.\n",
            EXIT_OK,
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
            try:
                self.writer.call(
                    self._mark_unfinished_renders,
                    label="mark an unfinished render failed",
                    kind=StepKind.SHUTDOWN,
                )
            except Exception as unmarked:  # noqa: BLE001 — named; the stop goes on
                _log.warning(
                    "the shutdown could not mark an unfinished render failed (%s)",
                    type(unmarked).__name__,
                )
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
        if kind == " ".join(SUMMARIZE_WORDS):
            self._summary_job(job_id, parsed, kind)
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

    def _summary_job(self, job_id: str, parsed: Parsed, kind: str) -> None:
        """`transcript summarize` as a job whose result arrives when the model has answered (7b).

        The command runs here, on this worker: its checks, the row's move to a
        new version, and the job's start. Then this worker is free — it never
        waits on the model pool (AD-19) — and the job's row is settled by
        whichever finishes last, this command or the summary job, with the
        command's output and the summary's closing line.
        """
        pending = _PendingSummary(lambda output, code: self._settle(job_id, output, code))
        out, err = io.StringIO(), io.StringIO()
        try:
            fresh = self._fresh(job_id)
        except ConfigRefused as refused:
            pending.returned(f"pm-ai: {refused}\n", EXIT_REFUSAL)
            return
        handed = handed_daemon(fresh, hand=self._hand_for(job_id))

        def summarize(
            *,
            scope: DataScope,
            meeting_id: str | None,
            by: str,
            title: str | None = None,
            on: date | None = None,
            again: bool = False,
        ) -> Any:
            # Story 11f: `--meeting` wins; otherwise `--title [--on]` chooses
            # exactly one meeting, through the listing's own reading code.
            if meeting_id is not None:
                ignored = [
                    f"`--{name}`" for name, given in (("title", title), ("on", on))
                    if given is not None
                ]
                chosen = (
                    f"{' and '.join(ignored)} {'was' if len(ignored) == 1 else 'were'} "
                    f"not consulted: `--meeting` names the meeting. "
                    if ignored
                    else ""
                )
            else:
                if title is None:
                    raise SummaryRefused("no meeting was named. Nothing was sent.")
                meeting_id, chosen = choose_meeting(
                    self._lister(fresh), scope=scope, title=title, on=on
                )
            requested = request_summary(
                handed,
                scope=scope,
                meeting_id=meeting_id,
                job=job_id,
                by=by,
                on_done=pending.finished,
                again=again,
                chosen=chosen,
            )
            pending.started()
            return requested

        try:
            code = dispatch(
                parsed,
                daemon=fresh,
                diagnose=self.diagnose,
                probe_connectors=None,
                transcript_summarize=summarize,
                out=out,
                err=err,
                terminal=False,
            )
        except BaseException as error:  # noqa: BLE001 — the row is always settled
            if isinstance(error, (WriterStopped, SchemaVersionChanged)):
                sentence = f"the {kind} job stopped: the daemon is stopping ({error})."
            elif isinstance(error, (ArtifactBusy, OperationalStoreUnavailable)):
                sentence = str(error)
            else:
                _log.warning(
                    "the %s job %s stopped on an unexpected %s",
                    kind, job_id, type(error).__name__, extra={"job": job_id},
                )
                sentence = _unexpected(error, doing=f"the {kind} job")
            # Settled whether or not the summary had started: a row left
            # `running` would hold a terminal following it for ever. Once the
            # summary has started, its own end settles the row with this line.
            said = out.getvalue() + err.getvalue() + f"pm-ai: {sentence}\n"
            pending.returned(said, EXIT_UNEXPECTED)
            return
        pending.returned(out.getvalue() + err.getvalue(), code)

    def _settle(self, job_id: str, output: str, code: int) -> None:
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
            # `goal list` and `goal show` (story 4t): `strategic_goals.md` read
            # afresh for this line (AD-7), and only read — setting, updating and
            # deleting a goal are `TERMINAL_BOUND` and never reach the daemon.
            goal_reader=GoalReader(fresh.storage),
            # `member link` and `member unmatched` (story 7e): the ledger,
            # `aliases.md` and `8t`'s record read afresh for this line (AD-5);
            # the one append handed over. `member name` is `TERMINAL_BOUND`.
            members=MemberDesk(handed_daemon(fresh, hand=hand)),
            # `transcript list` (story 11f): the terminal's own reader, by path,
            # so the prompt prints what the terminal prints with a daemon running.
            meeting_list=self._lister(fresh),
            # Story 13a: the queue read by path as the terminal reads it; each
            # change one claimed step handed to the writer, `from the prompt`.
            proposals=self._proposals(fresh, job),
            unbuilt_connectors=fresh.unbuilt,
            out=out,
            err=err,
            terminal=False,
        )
        return out.getvalue() + err.getvalue(), code

    def _proposals(self, fresh: Daemon, job: str | None) -> ProposalDesk:
        """The proposal engine in the daemon (story 13a): its writes handed to the writer.

        Each change waits at most `WRITER_START_WAIT_SECONDS` for the writer
        thread to start it, inside `approve`, `reject` and `edit`'s 2-second
        bound; past that it is refused as busy, nothing written.
        """

        def hand(work: Callable[[], Any], label: str) -> Any:
            result = self.writer.call(
                work, label=label, job=job, start_within=WRITER_START_WAIT_SECONDS
            )
            self.notes.finished(job, label)
            return result

        paths = fresh.storage.paths
        return ProposalDesk(
            now=fresh.clock,
            paths=paths if isinstance(paths, ScopePaths) else None,
            storage=handed_daemon(fresh, hand=hand).storage,
            via="prompt",
            types=fresh.proposal_types,
            reachable=lambda: True,
        )

    def _lister(self, fresh: Daemon) -> MeetingLister:
        """`transcript list`'s reader in the daemon: this daemon answers (story 11f).

        Over the daemon's own resolver, as `listed_after_drop` reads, with the
        projects `projects.toml` enrols now.
        """
        paths = fresh.storage.paths
        return MeetingLister(
            reachable=lambda: True,
            now=fresh.clock,
            paths=paths if isinstance(paths, ScopePaths) else None,
        )

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
        """A dashboard job's render: `render_dashboard` with the job's own door.

        A project scope here is one the daemon watches: `prompt` refused every
        other before the line was planned (story 4q).
        """
        return self.render_dashboard(fresh, scope, hand=self._hand_for(job))

    def render_slot(self, scope: DataScope) -> threading.Lock:
        """The lock that keeps one render of `scope` in flight at a time (9b)."""
        with self._render_slots_lock:
            return self._render_slots.setdefault(scope, threading.Lock())

    def render_dashboard(
        self,
        fresh: Daemon,
        scope: DataScope,
        *,
        hand: Callable[[Callable[[], Any], str], Any],
        budget: float | None = None,
    ) -> Path:
        """The render as steps (AD-5, stories 4r, 9b), on a worker — every path's.

        A dashboard job and the 07:00 timer both come here. Inside the scope's
        render slot, so the clock is read only once the render may run: the
        reads and the render are bounded steps, each calendar read held to the
        per-connector fetch budget (`budget`, or `render_budget`); `git` is
        asked in a bounded step just before the write and its answer handed
        over with the bytes; the save and the render's record are one claimed
        step handed through `hand`, the record committed last. A failure is
        recorded for the scope, through `hand`, and raised; the writer stopping
        under it is raised unrecorded, and a render still before its claimed
        step at shutdown is recorded failed by the shutdown's own step.
        """
        assert_dashboard_scope(scope)
        rendering = _Rendering(scope)

        def claimed(work: Callable[[], Any], label: str) -> Any:
            def marked() -> Any:
                try:
                    return work()
                finally:
                    # On the writer thread: the shutdown's step, queued after
                    # this one, sees it and marks nothing.
                    with rendering.lock:
                        rendering.claimed = True

            return hand(marked, label)

        # Registered before it waits for the slot, so a render still waiting
        # behind another at shutdown is marked too.
        with self._render_slots_lock:
            self._renders.add(rendering)
        try:
            with self.render_slot(scope):
                with rendering.lock:
                    rendering.started = True
                now = fresh.clock()
                try:
                    document = prepare_dashboard(
                        fresh,
                        scope=scope,
                        now=now,
                        bound=self._render_bound(rendering, budget),
                    )
                    exclusion = ask_dashboard_exclusion(fresh, scope=scope)
                    published: list[Path] = []

                    def publish() -> Path:
                        published.append(
                            publish_dashboard(
                                fresh,
                                scope=scope,
                                document=document,
                                at=now,
                                exclusion=exclusion,
                            )
                        )
                        return published[-1]

                    try:
                        written: Path = claimed(publish, f"save the {scope} dashboard")
                    except (WriterStopped, SchemaVersionChanged, RenderUnrecorded):
                        raise
                    except Exception as failed:
                        if published:
                            # The step's commit failed after the rename: the
                            # page is new, only its record is missing.
                            raise render_unrecorded(
                                scope, failed, path=published[0]
                            ) from failed
                        raise
                except (WriterStopped, SchemaVersionChanged):
                    raise
                except Exception as failed:
                    record_failed_render(fresh, scope, failed, at=now, hand=claimed)
                    raise
                return written
        finally:
            with rendering.lock:
                rendering.finished = True
            with self._render_slots_lock:
                self._renders.discard(rendering)

    def _render_bound(self, rendering: _Rendering, budget: float | None) -> ReadBound:
        """Each calendar read of one render held to the per-connector budget (AD-5)."""
        limit = self.render_budget if budget is None else budget

        def bound(call: Callable[[], _T], label: str) -> _T:
            with rendering.lock:
                rendering.in_fetch = True
            try:
                try:
                    return run_bounded(call, timeout=limit, label=label)
                except TimeoutError:
                    raise FetchAbandoned(
                        f"{label} did not answer within its {limit:g} s budget and "
                        f"was abandoned, so that calendar could not be read."
                    ) from None
            finally:
                with rendering.lock:
                    rendering.in_fetch = False

        return bound

    def _mark_unfinished_renders(self) -> None:
        """The shutdown's own step: a render not yet at its claimed step is
        recorded failed, and writes nothing after — the stopping writer refuses
        its save (story 9b)."""
        with self._render_slots_lock:
            renders = tuple(self._renders)
        failures: list[tuple[DataScope, str]] = []
        for rendering in renders:
            with rendering.lock:
                if rendering.finished or rendering.claimed:
                    continue
                where = (
                    "reading the calendar"
                    if rendering.in_fetch
                    else "preparing it"
                    if rendering.started
                    else "waiting for another render of it to finish"
                )
            scope = rendering.scope
            failures.append(
                (
                    scope,
                    f"the daemon stopped while the {scope} render was {where}, so "
                    f"this attempt wrote nothing; the dashboard file was not "
                    f"replaced by it.",
                )
            )
        if not failures:
            return
        # One step, each record its own statement: one that fails is logged
        # and undone alone, never the others' marks.
        recorded = self.daemon.storage.record_render_failures(failures, at=self.daemon.clock())
        for scope in recorded:
            _log.warning(
                "the %s render was unfinished when the daemon stopped; recorded failed",
                scope,
            )

    def _hand_for(self, job: str | None) -> Callable[[Callable[[], Any], str], Any]:
        """The writer's door for one command: each step named, and noted for its job."""

        def hand(work: Callable[[], Any], label: str) -> Any:
            result = self.writer.call(work, label=label, job=job)
            self.notes.finished(job, label)
            return result

        return hand


