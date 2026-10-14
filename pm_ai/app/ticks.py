"""The scheduled harvest's timers — temporary; story 10a replaces this file (story 9a).

**An in-memory timer, which story 10a forbids** ("every job is a queue row, never
an in-memory timer"). It is here because CAP-2's four-hour cycle needed an
unattended process before the durable queue exists; 10a's schedule trigger
deletes this file whole. The arithmetic it uses — the cycle, the start spread,
the doubling waits — lives in `pm_ai.core.cadence`, which 10a keeps. Stated
cost: the doubling waits live here, in memory, so a restart starts them over;
the provider's own wait is saved with the failure and survives it.

## What it does

One timer per connector instance, all on the daemon's one event loop (AD-19),
attached in `pm_ai.app.daemon._lifespan`. The task wakes at the earliest of
every instance's due instant and the next read of the connector list.

- **The list is read** (AD-5's refresh point: `config.toml`, the connector
  rows and credentials; projects only at start) at every wake. It is read
  again at least once per cycle; within a minute when the last read failed;
  and every minute while no listed instance is set up, so a first enrolment,
  or a credential given to a built-in, is picked up within a minute.
- **Instances the list names for the first time** get a timer, the batch
  spread across the next fifteen minutes one after the other — at the
  daemon's start that is every instance; later it is usually one, due at
  that read — and never sooner than a wait its provider asked for before a
  restart (read back from its recorded failure). One the list no longer names
  loses its timer. A row filing into a project enrolled after the start is
  not built, and is named in the daemon's log with the shared restart
  sentence, once per cycle until a restart.
- **One harvest at a time**, on the daemon's I/O pool: the attempt reads its
  instance afresh and runs `pipelines.harvest_once` with every write handed to
  the writer thread as a claimed step and every fetch bounded by the
  per-connector budget, which the calendar fetch and the transcript fetch of
  one attempt share; the transcript ingestion has a budget of its own.
  Instances due together run one after another; none is
  lost. A settings or credentials read that fails is recorded against the
  instance; nothing an attempt raises stops the round.
- **The next attempt** is due 240 minutes after this one finished, ± up to 15,
  drawn each time; after a failure that can clear on its own, 1, 2, 4, …
  minutes (at most 240, never sooner than the provider asked); after a save
  another command refused, one minute; after a failure that needs a person,
  the next regular cycle — tried once each cycle, so a fix by hand is noticed.
  An instance skipped as not set up waits for its next cycle, or for the list
  read that finds it set up, whichever is first.

## Stopping

The task is cancelled with the other timers. A harvest still in a bounded step
— a fetch or the transcript ingestion — keeps its worker; the shutdown's own
step (`RequestPath.shutdown_marks`) records it failed, naming the last claimed
step it finished, and the writer refuses any step it hands over later. A step
abandoned at its budget is refused the same way, by thread, checked again on
the writer thread when its handed step runs.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, TypeVar, cast

from pm_ai.app.concurrency import WriterStopped
from pm_ai.app.pipelines import (
    HARVEST_FETCH_BUDGET_SECONDS,
    AttemptOutcome,
    FetchAbandoned,
    FetchBound,
    HarvestAttempt,
    harvest_once,
    record_failed,
    set_up,
)
from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
from pm_ai.app.wiring import HANDED_CALLS, handed_daemon
from pm_ai.connectors.graph.auth import StoreBusy
from pm_ai.connectors.registry import run_bounded
from pm_ai.core.cadence import (
    CYCLE,
    FIRST_RETRY,
    REFUSED_SAVE_RETRY,
    first_attempts,
    next_cycle,
    provider_floor,
    retries,
    retry_wait,
)
from pm_ai.core.config import ConfigRefused
from pm_ai.domain.harvest import HarvestFailure
from pm_ai.domain.health import UnbuiltConnector
from pm_ai.ports import ArtifactBusy
from pm_ai.storage.service import OperationalStoreBusy

__all__ = ["HarvestTimers"]

_log = logging.getLogger(DAEMON_LOGGER)
_T = TypeVar("_T")

NAP_SECONDS = 60.0
"""The longest one wait sleeps before the clock is read again, so a laptop that
slept through a due instant harvests within a minute of waking."""

SAVE_STEP = HANDED_CALLS["save_cursor"][0]
"""The claimed step that saves a harvest's position, coverage and failure."""

_CLEARS_BY_WAITING = (OSError, ArtifactBusy, OperationalStoreBusy, StoreBusy)
"""Failures reading an instance's settings and credentials that waiting clears:
a busy file or store, an I/O error. Anything else needs a person."""


@dataclass
class _Slot:
    """One instance's timer: when it is due, and its retryable failures in a row."""

    due: datetime
    failures_in_a_row: int = 0
    waiting_for_setup: bool = False
    """Its last attempt was skipped as not set up."""


@dataclass(frozen=True)
class _Listing:
    """One read of the connector list: each instance, whether it is set up, and
    its recorded failure; and every row not built, with why."""

    set_up: Mapping[str, bool]
    failures: Mapping[str, HarvestFailure | None]
    unbuilt: tuple[UnbuiltConnector, ...]


@dataclass(eq=False)
class _Running:
    """The harvest in flight: what it last finished, and what was abandoned."""

    instance: str
    last: str | None = None
    in_fetch: bool = False
    """Inside a bounded step now — what the shutdown records as abandoned."""
    finished: bool = False
    saved: bool = False
    """It has saved its position: a failure recorded now takes that success back."""
    abandoned: set[int] = field(default_factory=set)
    """The threads whose bounded step a deadline abandoned: their claimed steps
    are refused from then on."""
    lock: threading.Lock = field(default_factory=threading.Lock)


class _Raised:
    """What a bounded call raised, carried out as a value so that the only
    `TimeoutError` out of `run_bounded` is its deadline's."""

    def __init__(self, error: BaseException) -> None:
        self.error = error


class HarvestTimers:
    """The daemon's scheduled harvests: one timer per instance, one harvest at a time.

    `now`, `draw` and `sleep` are injected so a test drives the cycle with a
    fake clock; `budget` is the per-connector fetch budget, in seconds.
    """

    def __init__(
        self,
        *,
        requests: RequestPath,
        now: Callable[[], datetime],
        draw: Callable[[], float] = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        budget: float = HARVEST_FETCH_BUDGET_SECONDS,
        nap: float = NAP_SECONDS,
    ) -> None:
        self.requests = requests
        self.now = now
        self.draw = draw
        self.sleep = sleep
        self.budget = budget
        self.nap = nap
        self._slots: dict[str, _Slot] = {}
        self._reread_at: datetime | None = None
        self._named: dict[str, datetime] = {}
        self._running: _Running | None = None
        self._lock = threading.Lock()
        requests.shutdown_marks.append(self._mark_unfinished)

    # ── what a test reads ────────────────────────────────────────────────────

    @property
    def due(self) -> Mapping[str, datetime]:
        """Each instance's next due instant."""
        return {instance: slot.due for instance, slot in self._slots.items()}

    @property
    def reread_at(self) -> datetime | None:
        """When the connector list is read next, at the latest."""
        return self._reread_at

    # ── the loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Until cancelled: wait for the next wake, read the list, run what is due.

        Nothing but cancellation ends it: anything else a round raises is
        logged by type and the round tried again in a minute.
        """
        while True:
            try:
                await self._wait()
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as unexpected:  # noqa: BLE001 — logged; tried again shortly
                self._unexpected(unexpected, "a round of scheduled harvests")

    async def start(self) -> None:
        """Read the list and give every instance its timer, without running any."""
        self._reconcile(await self.requests.pool.run(self._list))

    async def tick(self) -> None:
        """Read the list again, then run every due instance, one after another."""
        self._reconcile(await self.requests.pool.run(self._list))
        while True:
            now = self.now()
            ready = sorted(
                (slot.due, instance) for instance, slot in self._slots.items() if slot.due <= now
            )
            if not ready:
                return
            _, instance = ready[0]
            attempt = await self.requests.pool.run(functools.partial(self._attempt, instance))
            self._settle(attempt)

    def _reconcile(self, listing: _Listing | None) -> None:
        now = self.now()
        if listing is None:
            # Unreadable: the timers kept, the list read again in a minute.
            self._reread_at = now + FIRST_RETRY
            return
        for unbuilt in listing.unbuilt:
            named = self._named.get(unbuilt.instance)
            if named is None or now - named >= CYCLE:
                self._named[unbuilt.instance] = now
                # A row filing into a project enrolled since the start says so
                # with the shared restart sentence, once a cycle until a restart.
                _log.warning(
                    "%s is not harvested: %s",
                    unbuilt.instance,
                    unbuilt.reason,
                    extra={"instance": unbuilt.instance},
                )
        for gone in set(self._slots) - set(listing.set_up):
            del self._slots[gone]
        new = [instance for instance in sorted(listing.set_up) if instance not in self._slots]
        for instance, due in first_attempts(new, now).items():
            # The provider's wait survives a restart; only the doubling starts over.
            floor = provider_floor(listing.failures.get(instance))
            self._slots[instance] = _Slot(due if floor is None or floor <= due else floor)
        for instance, slot in self._slots.items():
            if slot.waiting_for_setup and listing.set_up[instance]:
                slot.waiting_for_setup = False
                slot.due = min(slot.due, now)
        settled = any(listing.set_up.values())
        self._reread_at = now + (CYCLE if settled else FIRST_RETRY)

    def _next_wake(self) -> datetime:
        wakes = [slot.due for slot in self._slots.values()]
        wakes.append(self._reread_at or self.now())
        return min(wakes)

    async def _wait(self) -> None:
        while True:
            remaining = (self._next_wake() - self.now()).total_seconds()
            if remaining <= 0:
                return
            await self.sleep(min(remaining, self.nap))

    def _unexpected(self, error: BaseException, doing: str) -> None:
        _log.error(
            "%s stopped on an unexpected %s; it is tried again in a minute",
            doing,
            type(error).__name__,
        )
        retry = self.now() + FIRST_RETRY
        self._reread_at = retry
        for slot in self._slots.values():
            if slot.due < retry:
                slot.due = retry

    def _settle(self, attempt: HarvestAttempt) -> None:
        """When `attempt`'s instance is next due, from how the attempt ended."""
        slot = self._slots.get(attempt.instance)
        if slot is None:
            return
        finished = self.now()
        instance = attempt.instance
        failure = attempt.failure
        if attempt.outcome is AttemptOutcome.REFUSED:
            # Not a connector failure and not on the ladder: the first wait.
            slot.due = finished + REFUSED_SAVE_RETRY
            _log.warning(
                "%s's harvest attempt failed: a save was refused (%s); it is "
                "tried again in a minute",
                instance,
                attempt.refused,
                extra={"instance": instance},
            )
            return
        if failure is not None and attempt.outcome is AttemptOutcome.FAILED and retries(failure):
            slot.failures_in_a_row += 1
            wait = retry_wait(slot.failures_in_a_row, failure.retry_after)
            slot.due = finished + wait
            _log.warning(
                "%s's harvest attempt failed in a way that can clear on its own; "
                "it is tried again in %s (`pm-ai doctor` names the reason)",
                instance,
                _minutes(wait),
                extra={"instance": instance},
            )
            return
        slot.failures_in_a_row = 0
        slot.waiting_for_setup = attempt.outcome is AttemptOutcome.SKIPPED
        slot.due = next_cycle(finished, self.draw())
        if attempt.outcome is AttemptOutcome.FAILED:
            _log.warning(
                "%s's harvest attempt failed in a way that needs a person (`pm-ai "
                "doctor` names the reason and the remedy); it is tried again at "
                "its next regular cycle",
                instance,
                extra={"instance": instance},
            )
        elif attempt.outcome is AttemptOutcome.SUCCEEDED:
            persisted = attempt.persisted
            _log.info(
                "%s harvested: %d event(s) persisted",
                instance,
                0 if persisted is None else persisted.persisted,
                extra={"instance": instance},
            )

    # ── on a worker ──────────────────────────────────────────────────────────

    def _list(self) -> _Listing | None:
        """The instances to time, read now; `None` when the list cannot be read.

        On `None` the timers keep the list they have, and each attempt records
        its own refusal against its instance.
        """
        try:
            fresh = self.requests.fresh()
        except Exception as unreadable:  # noqa: BLE001 — logged; read again in a minute
            _log.warning(
                "the connector list was not read again (%s); the instances already "
                "timed keep their timers, and it is read again in a minute",
                unreadable if isinstance(unreadable, ConfigRefused) else type(unreadable).__name__,
            )
            return None
        try:
            records = self.requests.daemon.storage.harvest_records()
        except Exception as unreadable:  # noqa: BLE001 — no recorded waits to honour
            _log.warning(
                "the recorded harvest failures could not be read (%s); a provider's "
                "wait from before is not honoured this time",
                type(unreadable).__name__,
            )
            records = ()
        return _Listing(
            set_up={instance: set_up(connector) for instance, connector in fresh.connectors.items()},
            failures={record.instance: record.failure for record in records},
            unbuilt=fresh.unbuilt,
        )

    def _attempt(self, instance: str) -> HarvestAttempt:
        """One harvest of `instance`, read afresh. Never raises."""
        running = _Running(instance)
        with self._lock:
            self._running = running
        hand = functools.partial(self._hand, running)
        try:
            try:
                fresh = self.requests.fresh()
            except Exception as unreadable:  # noqa: BLE001 — recorded against it
                return record_failed(
                    handed_daemon(self.requests.daemon, hand=hand),
                    instance,
                    _unreadable(instance, unreadable),
                )
            return harvest_once(
                handed_daemon(fresh, hand=hand),
                instance,
                bound=self._bound_for(running),
                last_step=lambda: running.last,
            )
        except Exception as unexpected:  # noqa: BLE001 — the round goes on
            return HarvestAttempt(
                instance,
                AttemptOutcome.REFUSED,
                refused=f"the attempt stopped on an unexpected {type(unexpected).__name__}",
            )
        finally:
            with running.lock:
                running.finished = True
            with self._lock:
                if self._running is running:
                    self._running = None

    def _hand(self, running: _Running, work: Callable[[], Any], label: str) -> Any:
        """The writer's door for one harvest: refused from a thread a deadline abandoned.

        Checked here, and again on the writer thread when the step runs, so a
        thread abandoned while its step waited in the queue commits nothing.
        """
        handing = threading.get_ident()

        def refuse() -> WriterStopped:
            sentence = (
                f"the claimed step {label!r} of {running.instance}'s harvest was "
                f"refused: the bounded step that handed it over was abandoned at "
                f"its deadline"
            )
            _log.warning(sentence, extra={"instance": running.instance, "step": label})
            return WriterStopped(sentence)

        def abandoned() -> bool:
            with running.lock:
                return handing in running.abandoned

        if abandoned():
            raise refuse()

        def guarded() -> Any:
            if abandoned():
                raise refuse()
            return work()

        result = self.requests.writer.call(guarded, label=label)
        with running.lock:
            running.last = label
            if label == SAVE_STEP:
                running.saved = True
        return result

    def _bound_for(self, running: _Running) -> FetchBound:
        """The attempt's bound: its fetches share one per-connector budget (the
        calendar fetch's leftover is the transcript fetch's), and a step with
        `own_budget` (the ingestion) gets a whole one of its own."""
        spent = [0.0]

        def bound(call: Callable[[], _T], label: str, *, own_budget: bool = False) -> _T:
            if own_budget:
                return self._bounded(running, call, label, self.budget)
            left = self.budget - spent[0]
            started = time.monotonic()
            try:
                return self._bounded(running, call, label, left)
            finally:
                spent[0] += time.monotonic() - started

        return bound

    def _bounded(
        self, running: _Running, call: Callable[[], _T], label: str, timeout: float | None = None
    ) -> _T:
        """`call` as a bounded step under the per-connector fetch budget (AD-5).

        Past the budget the step is abandoned: its thread keeps running, and
        every claimed step it hands over from then on is refused. A credential
        save it hands over is still made — that goes to the writer through the
        credential store, not through this harvest's door. What `call` raises
        comes out as a value, so a `TimeoutError` out of `run_bounded` is
        always its deadline's: a deadline always raises `FetchAbandoned`.
        """
        timeout = self.budget if timeout is None else timeout
        if timeout <= 0:
            # Nothing left of the attempt's budget: abandoned before it starts.
            raise FetchAbandoned(
                f"{label} did not finish within its budget: nothing was left of the "
                f"attempt's {self.budget:g} s fetch budget, so it was not started "
                f"and this attempt failed as a timeout."
            )
        threads: list[int] = []

        def tracked() -> _T | _Raised:
            threads.append(threading.get_ident())
            try:
                return call()
            except BaseException as raised:  # noqa: BLE001 — carried out, re-raised below
                return _Raised(raised)

        with running.lock:
            running.in_fetch = True
        try:
            try:
                answer = run_bounded(tracked, timeout=timeout, label=label)
            except TimeoutError:
                with running.lock:
                    running.abandoned.update(threads)
                raise FetchAbandoned(
                    f"{label} did not finish within its budget ({timeout:g} s left of "
                    f"the attempt's {self.budget:g} s) and was abandoned, so this "
                    f"attempt failed as a timeout."
                ) from None
            if isinstance(answer, _Raised):
                raise answer.error
            return cast(_T, answer)
        finally:
            with running.lock:
                running.in_fetch = False

    # ── on the writer thread, at shutdown ────────────────────────────────────

    def _mark_unfinished(self) -> None:
        """The shutdown's own step: a harvest still in a bounded step is recorded failed."""
        with self._lock:
            running = self._running
        if running is None:
            return
        with running.lock:
            # Only a harvest inside a bounded step: one between claimed steps
            # has its next step refused by the stopping writer, and one whose
            # step was queued finished it before this step ran.
            if running.finished or not running.in_fetch:
                return
            last = running.last
            saved = running.saved
        instance = running.instance
        failure = HarvestFailure(
            reason=(
                f"the daemon stopped while {instance}'s harvest was in a bounded "
                f"step, so the step was abandoned. "
                + (
                    f"The last claimed step it finished was {last!r}; what it saved "
                    f"before stays."
                    if last
                    else "It had finished no claimed step, so nothing was saved."
                )
            ),
            retryable=True,
        )
        self.requests.daemon.storage.record_harvest_failure(
            instance, failure, after_save=saved
        )
        _log.warning(
            "%s's harvest was still in a bounded step when the daemon stopped; it "
            "was recorded failed",
            instance,
            extra={"instance": instance},
        )


def _unreadable(instance: str, error: BaseException) -> HarvestFailure:
    """The failure recorded when an instance's settings or credentials cannot be read."""
    if isinstance(error, ConfigRefused):
        # Broken configuration needs a person: recorded at once.
        return HarvestFailure(
            reason=f"config.toml could not be used, so {instance} was not harvested: {error}",
            retryable=False,
        )
    clears = isinstance(error, _CLEARS_BY_WAITING)
    return HarvestFailure(
        reason=(
            f"{instance} was not harvested: reading its settings and credentials "
            f"raised {type(error).__name__}. "
            + (
                "Waiting may clear it; the daemon tries again on its own."
                if clears
                else "pm-ai cannot classify that, so it needs a person."
            )
        ),
        retryable=clears,
    )


def _minutes(wait: timedelta) -> str:
    minutes = wait.total_seconds() / 60
    return f"{minutes:g} minute{'s' if minutes != 1 else ''}"
