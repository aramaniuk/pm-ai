"""The daemon's one writer thread, its I/O pool of four and its model pool of one (AD-5, AD-19).

One event loop waits and schedules; nothing on it blocks. Everything that does
block goes to one of three places, and only these three:

- **The writer thread** makes every write and enters every claim. Work reaches
  it as a *claimed step*: a function handed over whole, run to completion on
  that thread, its answer handed back. A claimed step is short and never waits
  — no network, keychain or `git` call, no sleep — and it tries a claim once.
  Because every claim is entered on this one thread, AD-5's per-thread re-entry
  guard means what it says inside the daemon. A step handed over *from* the
  writer thread runs at once rather than queueing behind itself.
- **The I/O pool**: four threads that run *bounded steps* — a calendar read, a
  provider call, a `git` question, a keychain probe — and every command a
  client sends. They never call the writer directly: they hand a claimed step
  to it and wait for the answer, exactly as the loop does.
- **The model pool** (story 7a): one thread for heavy local-model jobs, so
  only one generation runs at a time. A model job hands its write to the
  writer only through `ModelPool.hand_off`, which refuses once the shutdown
  has begun; the shutdown closes the model's connections, a call in flight
  included.

None of the three is `asyncio.to_thread` or the default executor, and none
keeps the process alive at exit: `concurrent.futures.ThreadPoolExecutor` joins
its threads when the interpreter stops, so a calendar read abandoned at a
deadline would hold the process open. The daemon waits for the writer
explicitly, at the end of its own stop sequence, and abandons the pools.

## Withdrawal, and why a busy answer leaves nothing behind

A client request waits at most one second for the writer thread to *start* its
step. Past that it is answered busy (HTTP 503) — and the step is withdrawn if
the writer has not started it, so no row appears for a request that was told
`503`. A step the writer already started is short and is awaited to its end.

## Stopping

`stop_accepting` begins the shutdown: from then on only a credential save and
the shutdown's own step are taken; anything else is refused and logged by
name. `close` lets the writer finish the step it is running and every step
already queued — with no time limit — and then joins it. A schema-version stop
(`SchemaVersionMoved` out of any step) halts the writer at once: every later
step is refused, so nothing more is written to the store.
"""

from __future__ import annotations

import asyncio
import collections
import contextvars
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, InvalidStateError
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

__all__ = [
    "IO_POOL_SIZE",
    "WRITER_START_WAIT_SECONDS",
    "IOPool",
    "MODEL_POOL_SIZE",
    "ModelJobAbandoned",
    "ModelPool",
    "StepKind",
    "WriterBusy",
    "WriterStopped",
    "WriterThread",
]

_T = TypeVar("_T")

IO_POOL_SIZE = 4
"""The I/O pool's threads (AD-19). A constant: a fifth job waits `queued`."""

WRITER_START_WAIT_SECONDS = 1.0
"""How long a client request waits for the writer thread to start its step (AD-5)."""


class StepKind(Enum):
    """What a claimed step is, which decides whether a stopping writer takes it."""

    ORDINARY = "ordinary"
    CREDENTIAL_SAVE = "credential save"
    """A refreshed credential's compare-and-save, still made while the writer is open."""
    SHUTDOWN = "shutdown"
    """The shutdown's own step, marking still-running jobs `failed`."""


class WriterBusy(Exception):
    """The writer thread did not start the step in time, and it was withdrawn."""


class WriterStopped(Exception):
    """The writer is stopping or closed, so the step was refused and logged by name."""


@dataclass(eq=False)
class _Step:
    work: Callable[[], Any]
    label: str
    kind: StepKind
    job: str | None
    started: Future[None] = field(default_factory=Future)
    done: Future[Any] = field(default_factory=Future)
    withdrawn: bool = False


class WriterThread:
    """The daemon's one writer thread: claimed steps, one at a time, in order.

    `step_scope` wraps every step on the writer thread — the daemon's is the
    store's `claimed_step`, which commits what a step wrote when it returns and
    rolls it back when it raises. An error of a type in `halts_on` (a
    schema-version stop, a transaction that would not roll back) halts the
    writer: `on_halt` is called once, on the writer thread, with it and the
    job its step belonged to, and every later step is refused.
    """

    def __init__(
        self,
        *,
        log: logging.Logger,
        step_scope: Callable[[], AbstractContextManager[object]] | None = None,
        halts_on: tuple[type[BaseException], ...] = (),
        on_halt: Callable[[BaseException, str | None], None] | None = None,
        name: str = "pm-ai-writer",
    ) -> None:
        self._log = log
        self._step_scope = step_scope
        self._halts_on = halts_on
        self._on_halt = on_halt
        self._queue: collections.deque[_Step] = collections.deque()
        self._condition = threading.Condition()
        self._stopping = False
        self._closing = False
        self._closed = False
        self._halted: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread.start()

    @property
    def name(self) -> str:
        return self._thread.name

    @property
    def halted(self) -> BaseException | None:
        """What halted the writer — a schema-version stop, a stuck transaction — or `None`."""
        return self._halted

    def on_writer_thread(self) -> bool:
        return threading.get_ident() == self._thread.ident

    def stop_accepting(self) -> None:
        """Begin the shutdown: only credential saves and the shutdown's step from now on."""
        with self._condition:
            self._stopping = True

    def close(self) -> None:
        """Finish the running step and every queued one, with no time limit; then join.

        Idempotent. After it returns every step is refused and logged.
        """
        with self._condition:
            self._stopping = True
            self._closing = True
            self._condition.notify_all()
        if self._thread.is_alive() and not self.on_writer_thread():
            self._thread.join()
        with self._condition:
            self._closed = True
            leftover = list(self._queue)
            self._queue.clear()
        for step in leftover:
            self._refuse(step, "the writer closed before it was started")

    # ── handing a step over ──────────────────────────────────────────────────

    def call(
        self,
        work: Callable[[], _T],
        *,
        label: str,
        kind: StepKind = StepKind.ORDINARY,
        job: str | None = None,
        start_within: float | None = None,
    ) -> _T:
        """Hand `work` to the writer thread and wait for its answer.

        From the writer thread itself it runs at once. `start_within` bounds
        the wait for the step to *start* — past it the step is withdrawn and
        `WriterBusy` raised; a started step is awaited to its end.
        """
        if self.on_writer_thread():
            return work()
        step = self._submit(work, label=label, kind=kind, job=job)
        if start_within is not None:
            try:
                step.started.result(timeout=start_within)
            except TimeoutError:
                if self._withdraw(step):
                    raise WriterBusy(self._busy(label, start_within)) from None
        return step.done.result()  # type: ignore[no-any-return]

    async def call_async(
        self,
        work: Callable[[], _T],
        *,
        label: str,
        kind: StepKind = StepKind.ORDINARY,
        job: str | None = None,
        start_within: float | None = None,
    ) -> _T:
        """`call`, awaited from the event loop: the loop waits, the writer works.

        Cancelling the awaiter never reaches the writer's futures (each wait is
        shielded): cancelled before the step started, the step is withdrawn;
        cancelled after, the step runs to its end and its answer is dropped.
        """
        if self.on_writer_thread():
            return work()
        step = self._submit(work, label=label, kind=kind, job=job)
        started = asyncio.wrap_future(step.started)
        done = asyncio.wrap_future(step.done)
        try:
            # Started or answered, whichever comes first: a step the writer
            # refuses without starting (closed with it still queued) answers
            # without ever starting. `asyncio.wait` cancels neither on timeout
            # or on this task's cancellation.
            began, _ = await asyncio.wait(
                {started, done}, timeout=start_within, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            self._withdraw(step)
            raise
        if not began and self._withdraw(step):
            raise WriterBusy(self._busy(label, start_within or 0.0))
        return await asyncio.shield(done)  # type: ignore[no-any-return]

    @staticmethod
    def _busy(label: str, seconds: float) -> str:
        return (
            f"the daemon's writer did not start {label} within {seconds:g} s, so "
            f"it was withdrawn and nothing was written. Try again in a second."
        )

    def _submit(
        self, work: Callable[[], Any], *, label: str, kind: StepKind, job: str | None
    ) -> _Step:
        # Run in a copy of the handing thread's context, so what a command set
        # for itself there — its own output streams, that it has no terminal —
        # holds inside its claimed steps too, and never leaks into another's.
        context = contextvars.copy_context()
        step = _Step(work=lambda: context.run(work), label=label, kind=kind, job=job)
        with self._condition:
            if self._closed:
                refusal = "the writer has closed"
            elif self._halted is not None:
                refusal = self._halt_reason()
            elif self._stopping and kind is StepKind.ORDINARY:
                refusal = "the daemon is stopping"
            else:
                self._queue.append(step)
                self._condition.notify()
                return step
        self._refuse(step, refusal)
        raise WriterStopped(self._refused(step, refusal))

    def _withdraw(self, step: _Step) -> bool:
        with self._condition:
            if step.started.done():
                return False
            step.withdrawn = True
            try:
                self._queue.remove(step)
            except ValueError:
                pass
            return True

    def _refused(self, step: _Step, why: str) -> str:
        owner = f" for job {step.job}" if step.job else ""
        return f"the claimed step {step.label!r}{owner} was refused: {why}."

    def _refuse(self, step: _Step, why: str) -> None:
        self._log.warning(
            self._refused(step, why),
            extra={"step": step.label, "job": step.job},
        )
        if not step.done.done():
            step.done.set_exception(WriterStopped(self._refused(step, why)))

    # ── the thread ───────────────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closing:
                    self._condition.wait()
                if not self._queue:
                    return
                step = self._queue.popleft()
                if step.withdrawn:
                    continue
                if not step.started.set_running_or_notify_cancel():
                    step.done.cancel()
                    continue
                step.started.set_result(None)
                halted = self._halted
            if halted is not None:
                self._refuse(step, self._halt_reason())
                continue
            self._execute(step)

    def _halt_reason(self) -> str:
        halted = self._halted
        if halted is not None and type(halted).__name__.startswith("SchemaVersion"):
            return "the daemon stopped on a schema-version mismatch"
        return f"the writer halted ({type(halted).__name__})"

    def _execute(self, step: _Step) -> None:
        raised: BaseException | None = None
        result: Any = None
        try:
            if self._step_scope is None:
                result = step.work()
            else:
                with self._step_scope():
                    result = step.work()
        except BaseException as error:  # noqa: BLE001 — relayed to the waiter
            raised = error
            if self._halts_on and isinstance(error, self._halts_on):
                with self._condition:
                    first = self._halted is None
                    self._halted = error
                if first and self._on_halt is not None:
                    try:
                        self._on_halt(error, step.job)
                    except Exception:  # noqa: BLE001 — never kills the writer
                        self._log.exception("recording why the writer halted failed")
        # A waiter that stopped waiting may have cancelled its future already;
        # that is its business, never a reason for this thread to die.
        if step.done.done():
            return
        try:
            if raised is not None:
                step.done.set_exception(raised)
            else:
                step.done.set_result(result)
        except InvalidStateError:
            pass


class IOPool:
    """Four threads for bounded steps and client commands (AD-19).

    Not `ThreadPoolExecutor`: its threads are joined at interpreter exit, so a
    read abandoned at a deadline would keep the process alive. These threads
    are daemon threads, started once, fed from one queue in order.
    """

    def __init__(self, size: int = IO_POOL_SIZE, *, name: str = "pm-ai-io") -> None:
        self._work: collections.deque[tuple[Callable[[], Any], Future[Any]]] = (
            collections.deque()
        )
        self._ahead = 0
        """How many of the queued items at the front of `_work` were submitted ahead."""
        self._condition = threading.Condition()
        self._closed = False
        self._threads = [
            threading.Thread(target=self._run, name=f"{name}-{n}", daemon=True)
            for n in range(1, size + 1)
        ]
        self._started = False

    @property
    def size(self) -> int:
        return len(self._threads)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for thread in self._threads:
            thread.start()

    def submit(self, work: Callable[[], _T]) -> Future[_T]:
        future: Future[_T] = Future()
        with self._condition:
            if self._closed:
                raise RuntimeError("the daemon's I/O pool has stopped taking work")
            self._work.append((work, future))
            self._condition.notify()
        return future

    async def run(self, work: Callable[[], _T]) -> _T:
        """`work` on a pool thread, awaited from the loop."""
        return await asyncio.wrap_future(self.submit(work))

    def shutdown(self) -> None:
        """Take no more work; threads still running a bounded step are abandoned."""
        with self._condition:
            self._closed = True
            for _, future in self._work:
                future.cancel()
            self._work.clear()
            self._ahead = 0
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._work and not self._closed:
                    self._condition.wait()
                if not self._work:
                    return
                work, future = self._work.popleft()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = work()
            except BaseException as raised:  # noqa: BLE001 — relayed to the waiter
                future.set_exception(raised)
            else:
                future.set_result(result)


MODEL_POOL_SIZE = 1
"""The model pool's threads (AD-19): one heavy local-model job at a time. Not a setting."""


class ModelJobAbandoned(Exception):
    """The daemon stopped while this model job was queued or running; its answer is dropped."""


class ModelPool:
    """One daemon thread for heavy local-model jobs (AD-19, story 7a).

    Shaped like `IOPool`, with one thread and two differences.

    **The shutdown abandons the running job.** `abort` — the local adapter's
    `close` — is called whether or not a job is running, so the model's
    client is closed when the daemon stops and a request in flight loses its
    connection. Whatever the running job returns afterwards is replaced by
    `ModelJobAbandoned`, and work still queued is cancelled and never starts.

    **A model job's write goes through `hand_off`.** A job calls the model,
    then hands what it would write to the writer thread from inside
    `hand_off(step)`. The hand-off and the shutdown take one lock: a hand-off
    that began before the shutdown finishes (the shutdown waits for it — a
    claimed step is short), and once the shutdown has begun every hand-off is
    refused with `ModelJobAbandoned` before its step runs. So nothing a call
    abandoned by the shutdown would have written is written, whichever side
    of the call's end the shutdown lands on.

    **Work submitted `ahead` is queued before ordinary work** (story 7b,
    Andrei, 2026-10-20): a summary the PM asked for is not left behind the
    harvest's automatic ones, so its bound covers its own call and retry. It
    never interrupts the job running, and work submitted ahead keeps its own
    order.

    A job's deadline is the request's own, so the slot is held until the
    request has actually ended and a second job never overlaps a generation
    still running. A model call never runs on the loop, the writer thread or
    an I/O worker.
    """

    def __init__(
        self, *, abort: Callable[[], None] | None = None, name: str = "pm-ai-model"
    ) -> None:
        self._work: collections.deque[tuple[Callable[[], Any], Future[Any]]] = (
            collections.deque()
        )
        self._ahead = 0
        """How many of the queued items at the front of `_work` were submitted ahead."""
        self._condition = threading.Condition()
        self._gate = threading.Lock()
        self._closed = False
        self._stopping = False
        self._running: Future[Any] | None = None
        self._abort = abort
        self._thread = threading.Thread(target=self._run, name=f"{name}-1", daemon=True)
        self._started = False

    @property
    def size(self) -> int:
        return MODEL_POOL_SIZE

    @property
    def stopping(self) -> bool:
        """Whether the shutdown has begun: every hand-off from now on is refused."""
        return self._stopping

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def submit(self, work: Callable[[], _T], *, ahead: bool = False) -> Future[_T]:
        """Queue `work`; with `ahead`, after any work queued ahead and before the rest."""
        future: Future[_T] = Future()
        with self._condition:
            if self._closed:
                raise RuntimeError("the daemon's model pool has stopped taking work")
            if ahead:
                self._work.insert(self._ahead, (work, future))
                self._ahead += 1
            else:
                self._work.append((work, future))
            self._condition.notify()
        return future

    async def run(self, work: Callable[[], _T]) -> _T:
        """`work` on the model thread, awaited from the loop."""
        return await asyncio.wrap_future(self.submit(work))

    def hand_off(self, step: Callable[[], _T]) -> _T:
        """Run `step` — a model job's hand-off to the writer — unless the shutdown has begun."""
        with self._gate:
            if self._stopping:
                raise ModelJobAbandoned(
                    "the daemon began stopping before this model call's answer was "
                    "handed to the writer, so nothing was written."
                )
            return step()

    def shutdown(self) -> None:
        """Refuse every later hand-off, cancel what is queued, close the model's connections."""
        with self._gate:
            # Waits for a hand-off already running; none starts after this.
            self._stopping = True
        with self._condition:
            self._closed = True
            for _, future in self._work:
                future.cancel()
            self._work.clear()
            self._ahead = 0
            self._condition.notify_all()
        if self._abort is not None:
            try:
                self._abort()
            except Exception:  # noqa: BLE001 — the stop sequence goes on
                logging.getLogger(__name__).exception("closing the model connection failed")

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._work and not self._closed:
                    self._condition.wait()
                if not self._work:
                    return
                work, future = self._work.popleft()
                if self._ahead:
                    self._ahead -= 1
                if not future.set_running_or_notify_cancel():
                    continue
                self._running = future
            try:
                result = work()
            except BaseException as raised:  # noqa: BLE001 — relayed to the waiter
                outcome: tuple[bool, Any] = (False, raised)
            else:
                outcome = (True, result)
            with self._condition:
                self._running = None
                abandoned = self._closed
            if abandoned:
                future.set_exception(
                    ModelJobAbandoned(
                        "the daemon stopped while this model call ran; its connection "
                        "was closed and its answer dropped, so nothing was written."
                    )
                )
            elif outcome[0]:
                future.set_result(outcome[1])
            else:
                future.set_exception(outcome[1])
