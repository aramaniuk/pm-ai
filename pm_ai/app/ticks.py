"""The scheduled harvest's and the morning render's timers — temporary; story 10a
replaces this file (stories 9a, 9b).

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

## The morning render (story 9b)

`RenderTimers`, one task beside the harvests', renders every dashboard scope —
personal, then each project the daemon watches (the enrolled projects read at
its start, AD-5; one enrolled since waits for a restart) — at 07:00 in
`display_timezone`. Whenever
it evaluates, at start or after any wait, a scope with no successful render
since today's 07:00 is rendered at once if it is past 07:00 (`cadence`), so a
daemon started late, or a laptop that slept through the morning, renders on
arrival. It evaluates again at the next 07:00, at a pending retry, and at least
every fifteen minutes, so a changed zone or a hand-run render is noticed.

- `config.toml` is read again at every evaluation for the zone, and the whole
  daemon (`RequestPath.fresh`) at the start of every render (AD-5). With the
  zone unset nothing is scheduled: the refusal is recorded for every dashboard
  scope at every evaluation.
- Each render runs on the I/O pool through `RequestPath.render_dashboard`:
  each calendar read a bounded step under the per-connector fetch budget, the
  project copy's `git` question a bounded step, the save and its record one
  claimed step on the writer thread. One scope failing does not stop the next.
- A render that met something waiting can clear — another command's claim, a
  busy store, an I/O error, a locked keychain — is tried again on `9a`'s waits,
  1, 2, 4, … minutes, until it succeeds or the next 07:00, whoever recorded the
  refusal (a hand-run or a job included). One that failed on its own input
  waits for the next 07:00, unless its file goes missing; a `config.toml`
  refused inside a render is tried at the next evaluation. A deleted dashboard
  is rendered again past 07:00.

## Stopping

The task is cancelled with the other timers. A harvest still in a bounded step
— a fetch or the transcript ingestion — keeps its worker; the shutdown's own
step (`RequestPath.shutdown_marks`) records it failed, naming the last claimed
step it finished, and the writer refuses any step it hands over later. A step
abandoned at its budget is refused the same way, by thread, checked again on
the writer thread when its handed step runs.

The render task is cancelled the same way. A render not yet at its claimed step
— waiting for its scope's slot, reading the calendar, or preparing the page —
is recorded failed by the shutdown's own step
(`RequestPath._mark_unfinished_renders`), each record its own statement in that
one step, and the stopping writer refuses its save, so it writes no file after.
A render whose claimed step is running or queued finishes it and records; then
the writer closes.
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
from datetime import datetime, timedelta, tzinfo
from enum import Enum
from typing import Any, TypeVar, cast

from pm_ai.app.concurrency import WriterStopped
from pm_ai.app.pipelines import (
    HARVEST_FETCH_BUDGET_SECONDS,
    AttemptOutcome,
    FetchAbandoned,
    FetchBound,
    HarvestAttempt,
    DASHBOARD_ARTIFACT,
    clears_by_waiting,
    display_zone_now,
    harvest_once,
    record_failed,
    record_failed_render,
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
    latest_render,
    next_cycle,
    next_render,
    provider_floor,
    render_due,
    retries,
    retry_wait,
    todays_render,
)
from pm_ai.core.config import ConfigRefused
from pm_ai.domain.harvest import HarvestFailure
from pm_ai.domain.health import UnbuiltConnector
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import ArtifactBusy
from pm_ai.storage.service import OperationalStoreBusy, SchemaVersionChanged

__all__ = ["HarvestTimers", "RenderTimers"]

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
        if attempt.outcome is AttemptOutcome.FAILED and failure is not None and (
            failure.walk_cut_short
        ):
            _log.warning(
                "%s's harvest attempt stopped because a channel walk was cut "
                "short (`pm-ai doctor` names the channel, and lists it as failing "
                "until a walk completes); it waits for its next regular cycle, "
                "which walks the channel again",
                instance,
                extra={"instance": instance},
            )
        elif attempt.outcome is AttemptOutcome.FAILED:
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


# ── The morning render (story 9b) ────────────────────────────────────────────

REEVALUATE = timedelta(minutes=15)
"""The longest the render timer goes without evaluating again: a zone changed or
set, a render made by hand, or a dashboard file deleted is noticed within it.
Also the longest wait after an evaluation that could not run."""

PERSONAL = DataScope(ScopeKind.PERSONAL)


class RenderOutcome(Enum):
    """How one render attempt ended, which decides when the scope is due again."""

    RENDERED = "rendered"
    BUSY = "busy"
    """It met something waiting can clear — another command's claim, a busy
    store, an I/O error, a locked keychain: retried on `9a`'s waits until the
    next 07:00."""
    FAILED = "failed"
    """It failed on its own input — a malformed goals file, a refusal: the next 07:00."""
    REEVALUATE = "reevaluate"
    """`config.toml` was refused inside the render although the evaluation read
    a zone: tried again at the next evaluation, which reads it again."""
    STOPPED = "stopped"
    """The daemon's writer stopped under it: the round ends."""


def _outcome_of(error: BaseException) -> RenderOutcome:
    if isinstance(error, ConfigRefused):
        return RenderOutcome.REEVALUATE
    return RenderOutcome.BUSY if clears_by_waiting(error) else RenderOutcome.FAILED


@dataclass
class _Retry:
    """A scope's waitable failures in a row, within one 07:00's day."""

    day: datetime
    """The latest 07:00 when they began; the next 07:00 starts over."""
    due: datetime
    failures_in_a_row: int


@dataclass(frozen=True)
class _Plan:
    """One evaluation: the zone, or why there is none, and the scopes due now."""

    zone: tzinfo | None
    due: tuple[DataScope, ...]
    retry_at: datetime | None = None
    """When an evaluation that could not run is tried again."""


class RenderTimers:
    """The daemon's 07:00 render of every dashboard scope (story 9b).

    `now` and `sleep` are injected so a test drives the day with a fake clock;
    `budget`, when given, replaces the per-connector fetch budget each
    calendar read runs under.
    """

    def __init__(
        self,
        *,
        requests: RequestPath,
        now: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        budget: float | None = None,
        nap: float = NAP_SECONDS,
    ) -> None:
        self.requests = requests
        self.now = now
        self.sleep = sleep
        self.budget = budget
        self.nap = nap
        self._wake: datetime | None = None
        self._retries: dict[DataScope, _Retry] = {}
        self._given_up: dict[DataScope, datetime] = {}
        """Scopes whose render failed on its own input, by the latest 07:00 then."""
        self._today: dict[DataScope, datetime] = {}
        """Each scope's latest 07:00 at the last evaluation."""
        self._present: dict[DataScope, bool] = {}
        """Whether each scope's file was there at the last evaluation."""
        self._unreadable_in_a_row = 0
        """Evaluations in a row that could not read the zone or the records."""
        self._unexpected_in_a_row = 0
        """Rounds in a row that stopped on something unexpected."""

    # ── what a test reads ────────────────────────────────────────────────────

    @property
    def wake(self) -> datetime | None:
        """When the timer evaluates next."""
        return self._wake

    @property
    def scopes(self) -> tuple[DataScope, ...]:
        """Every dashboard scope, in render order: personal, then each project the
        daemon watches — read at its start (AD-5)."""
        return (
            PERSONAL,
            *(DataScope(ScopeKind.PROJECT, project) for project in self.requests.daemon.watched),
        )

    # ── the loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Until cancelled: evaluate, render what is due, wait for the next wake.

        A round that stops on something unexpected is logged by type and tried
        again after 1, 2, 4 … minutes, at most `REEVALUATE`.
        """
        while True:
            try:
                await self.tick()
                self._unexpected_in_a_row = 0
            except asyncio.CancelledError:
                raise
            except Exception as unexpected:  # noqa: BLE001 — logged; backed off
                self._unexpected_in_a_row += 1
                wait = min(retry_wait(self._unexpected_in_a_row), REEVALUATE)
                _log.error(
                    "an evaluation of the morning render stopped on an unexpected %s; "
                    "it is evaluated again in %s",
                    type(unexpected).__name__,
                    _minutes(wait),
                )
                self._wake = self.now() + wait
            await self._wait()

    async def tick(self) -> None:
        """Evaluate once: render every scope due now, one after another, in order."""
        plan = await self.requests.pool.run(self._plan)
        for scope in plan.due:
            outcome = await self.requests.pool.run(functools.partial(self._attempt, scope))
            self._settle(scope, outcome)
            if outcome is RenderOutcome.STOPPED:
                break
        self._wake = self._next_wake(plan)

    def _next_wake(self, plan: _Plan) -> datetime:
        now = self.now()
        wakes = [now + REEVALUATE]
        if plan.retry_at is not None:
            wakes.append(plan.retry_at)
        if plan.zone is not None:
            wakes.append(next_render(now, plan.zone))
        # An overdue retry wakes the timer at once, never at the next fifteen.
        wakes.extend(max(retry.due, now) for retry in self._retries.values())
        return min(wakes)

    async def _wait(self) -> None:
        while True:
            wake = self._wake if self._wake is not None else self.now()
            remaining = (wake - self.now()).total_seconds()
            if remaining <= 0:
                return
            await self.sleep(min(remaining, self.nap))

    def _settle(self, scope: DataScope, outcome: RenderOutcome) -> None:
        now = self.now()
        day = self._today.get(scope, now)
        if outcome is RenderOutcome.RENDERED:
            self._retries.pop(scope, None)
            self._given_up.pop(scope, None)
            _log.info("the %s dashboard was rendered", scope)
        elif outcome is RenderOutcome.BUSY:
            retry = self._retries.get(scope)
            count = 1 if retry is None or retry.day != day else retry.failures_in_a_row + 1
            wait = retry_wait(count)
            self._retries[scope] = _Retry(day=day, due=now + wait, failures_in_a_row=count)
            _log.warning(
                "the %s dashboard render met something waiting can clear (`pm-ai "
                "doctor` names it); it is tried again in %s",
                scope,
                _minutes(wait),
            )
        elif outcome is RenderOutcome.FAILED:
            self._retries.pop(scope, None)
            self._given_up[scope] = day
            _log.warning(
                "the %s dashboard render failed and is tried again at the next 07:00 "
                "(`pm-ai doctor` names the reason)",
                scope,
            )
        elif outcome is RenderOutcome.REEVALUATE:
            self._retries.pop(scope, None)
            _log.warning(
                "the %s dashboard render met a config.toml it could not use; it is "
                "tried again at the next evaluation",
                scope,
            )
        else:
            _log.warning(
                "the %s dashboard render stopped with the daemon's writer; the "
                "round ends",
                scope,
            )

    # ── on a worker ──────────────────────────────────────────────────────────

    def _plan(self) -> _Plan:
        """The zone read now, the records read now, and the scopes due now.

        With no zone, a `config.toml` the loader refuses, or a zone or records
        that cannot be read, nothing is due and the refusal is recorded for
        every dashboard scope; an unreadable read is tried again on the waits.

        A scope is due when it is past today's 07:00 with no success since; or
        past it with its file missing (a disposable artifact deleted is
        re-rendered); or when its latest record is a failure waiting can clear,
        newer than its last success and since the latest 07:00 — whoever
        recorded it, a hand-run or a job included — retried on `9a`'s waits.
        """
        now = self.now()
        daemon = self.requests.daemon
        self._today = {}
        try:
            zone = display_zone_now(daemon)
            records = {record.scope: record for record in daemon.storage.render_records()}
        except ConfigRefused as refused:
            self._unreadable_in_a_row = 0
            self._record_everywhere(refused, now)
            return _Plan(None, ())
        except Exception as unreadable:  # noqa: BLE001 — recorded; retried on the waits
            self._unreadable_in_a_row += 1
            self._record_everywhere(unreadable, now)
            wait = min(retry_wait(self._unreadable_in_a_row), REEVALUATE)
            _log.warning(
                "the morning render could not read its zone or its records (%s); it "
                "is evaluated again in %s",
                type(unreadable).__name__,
                _minutes(wait),
            )
            return _Plan(None, (), retry_at=now + wait)
        self._unreadable_in_a_row = 0
        today = todays_render(now, zone)
        day = latest_render(now, zone)
        due: list[DataScope] = []
        for scope in self.scopes:
            self._today[scope] = day
            record = records.get(scope)
            present = daemon.storage.paths.resolve(scope, DASHBOARD_ARTIFACT).exists()
            if self._present.get(scope, True) and not present:
                # The file went missing: whatever was given up is tried again.
                self._given_up.pop(scope, None)
            self._present[scope] = present
            # Past 07:00, a deleted page is rebuilt even if today's render is
            # recorded: the file is disposable (Tier 3), and what it shows is
            # whatever answers now.
            missing = now >= today and not present
            waitable = (
                record is not None
                and record.failing
                and record.retryable
                and record.last_failure is not None
                and record.last_failure >= day
            )
            last = None if record is None else record.last_success
            if not (missing or waitable or render_due(last, now, zone)):
                # Rendered since today's 07:00, by anyone, or not yet 07:00.
                self._retries.pop(scope, None)
                continue
            if self._given_up.get(scope) == day:
                continue
            retry = self._retries.get(scope)
            if retry is not None and retry.day != day:
                # A new 07:00: the waits start over.
                del self._retries[scope]
                retry = None
            if retry is None and waitable and record is not None and record.last_failure:
                # Refused by a claim or a busy store, recorded by anyone: the
                # first wait counts from that refusal.
                retry = _Retry(day=day, due=record.last_failure + FIRST_RETRY, failures_in_a_row=1)
                self._retries[scope] = retry
            if retry is not None and retry.due > now:
                continue
            due.append(scope)
        return _Plan(zone, tuple(due))

    def _record_everywhere(self, error: BaseException, now: datetime) -> None:
        for scope in self.scopes:
            record_failed_render(
                self.requests.daemon, scope, error, at=now, hand=self._hand_record
            )

    def _attempt(self, scope: DataScope) -> RenderOutcome:
        """One render of `scope`, the daemon read afresh. Never raises."""
        try:
            fresh = self.requests.fresh()
        except (WriterStopped, SchemaVersionChanged):
            return RenderOutcome.STOPPED
        except Exception as unreadable:  # noqa: BLE001 — recorded against the scope
            record_failed_render(
                self.requests.daemon, scope, unreadable, at=self.now(), hand=self._hand_record
            )
            return _outcome_of(unreadable)
        try:
            self.requests.render_dashboard(
                fresh, scope, hand=self._hand_record, budget=self.budget
            )
        except (WriterStopped, SchemaVersionChanged):
            return RenderOutcome.STOPPED
        except Exception as failed:  # noqa: BLE001 — recorded by `render_dashboard`
            return _outcome_of(failed)
        return RenderOutcome.RENDERED

    def _hand_record(self, work: Callable[[], Any], label: str) -> Any:
        return self.requests.writer.call(work, label=label)
