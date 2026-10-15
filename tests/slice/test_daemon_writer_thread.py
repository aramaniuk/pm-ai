"""The daemon's one writer thread and its I/O pool of four (AD-5, AD-19, story 4r).

Driven directly: the rules about who runs what, in which order, and what a
stopping writer still takes are properties of `pm_ai.app.concurrency`, not of
any one job.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest

from pm_ai.app.concurrency import (
    IO_POOL_SIZE,
    IOPool,
    StepKind,
    WriterBusy,
    WriterStopped,
    WriterThread,
)


class _Halt(Exception):
    pass


@pytest.fixture
def records(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="test.writer")
    return caplog


@pytest.fixture
def writer() -> WriterThread:
    started = WriterThread(log=logging.getLogger("test.writer"))
    started.start()
    yield started
    started.close()


def _hold(writer: WriterThread, *, label: str = "held") -> tuple[threading.Event, threading.Event, threading.Thread]:
    """Occupy the writer thread until the returned `release` is set."""
    entered, release = threading.Event(), threading.Event()

    def step() -> None:
        entered.set()
        release.wait(30)

    handing = threading.Thread(target=lambda: writer.call(step, label=label))
    handing.start()
    assert entered.wait(5)
    return entered, release, handing


def _until_queued(writer: WriterThread, count: int, *, within: float = 10.0) -> None:
    """Wait until `count` steps wait in the writer's queue behind the running one.

    Reads the writer's own queue under its own lock: a test's view of private
    state, so no hook in the writer is needed.
    """
    deadline = time.monotonic() + within
    while True:
        with writer._condition:
            waiting = len(writer._queue)
        if waiting >= count:
            assert waiting == count, f"{waiting} steps queued, expected {count}"
            return
        assert time.monotonic() < deadline, f"only {waiting} of {count} steps were queued"
        time.sleep(0.005)


def test_a_step_runs_on_the_writer_thread_and_its_answer_comes_back(writer):
    ran_on = writer.call(lambda: threading.current_thread().name, label="who")
    assert ran_on == writer.name
    with pytest.raises(KeyError):
        writer.call(lambda: {}["missing"], label="raise")


def test_a_step_handed_over_from_the_writer_thread_runs_at_once(writer):
    nested = writer.call(lambda: writer.call(lambda: "inner", label="inner"), label="outer")
    assert nested == "inner"


def test_steps_run_one_at_a_time_in_order(writer):
    seen: list[int] = []
    threads = [
        threading.Thread(target=lambda n=n: writer.call(lambda: seen.append(n), label=str(n)))
        for n in range(20)
    ]
    for thread in threads:
        thread.start()
        thread.join()
    assert seen == list(range(20))


def test_a_step_not_started_within_the_wait_is_withdrawn_and_never_runs(writer):
    _, release, handing = _hold(writer)
    ran = threading.Event()
    with pytest.raises(WriterBusy, match="withdrawn"):
        writer.call(ran.set, label="record a row", start_within=0.2)
    release.set()
    handing.join()
    writer.call(lambda: None, label="drain")
    assert not ran.is_set(), "a step the request stopped waiting for must never run"


def test_the_async_wait_withdraws_the_same_way(writer):
    _, release, handing = _hold(writer)
    ran = threading.Event()

    async def ask() -> None:
        await writer.call_async(ran.set, label="record a row", start_within=0.2)

    with pytest.raises(WriterBusy):
        asyncio.run(ask())
    release.set()
    handing.join()
    writer.call(lambda: None, label="drain")
    assert not ran.is_set()


def test_a_started_step_is_awaited_to_its_end_past_the_wait(writer):
    def slow() -> str:
        time.sleep(0.4)
        return "finished"

    assert writer.call(slow, label="slow", start_within=0.1) == "finished"


def test_a_stopping_writer_takes_only_credential_saves_and_the_shutdown_step(writer, records):
    writer.stop_accepting()
    with pytest.raises(WriterStopped, match="'save the dashboard' for job job_1"):
        writer.call(lambda: None, label="save the dashboard", job="job_1")
    assert writer.call(lambda: "saved", label="save a token", kind=StepKind.CREDENTIAL_SAVE) == "saved"
    assert writer.call(lambda: "marked", label="mark", kind=StepKind.SHUTDOWN) == "marked"
    assert any("save the dashboard" in r.getMessage() for r in records.records), "logged by name"


def test_close_finishes_the_running_step_and_every_queued_one_with_no_time_limit(writer):
    _, release, handing = _hold(writer)
    done: list[str] = []
    queued = [
        threading.Thread(target=lambda n=n: writer.call(lambda: done.append(n), label=n))
        for n in ("a", "b", "c")
    ]
    # One at a time, each started only once the writer has queued the one
    # before it, so the order the steps were handed over is the order asserted
    # — never the order three threads happened to be scheduled in.
    for count, thread in enumerate(queued, 1):
        thread.start()
        _until_queued(writer, count)
    closing = threading.Thread(target=writer.close)
    closing.start()
    closing.join(0.5)
    assert closing.is_alive(), "close waits for the running step, however long"
    release.set()
    closing.join(10)
    handing.join()
    for thread in queued:
        thread.join()
    assert done == ["a", "b", "c"]


def test_after_close_even_a_credential_save_is_refused_and_logged(writer, records):
    writer.close()
    with pytest.raises(WriterStopped, match="closed"):
        writer.call(lambda: None, label="save a token", kind=StepKind.CREDENTIAL_SAVE)
    assert any("save a token" in r.getMessage() for r in records.records)


def test_a_halting_error_refuses_every_later_step(records):
    halted: list[tuple[str, str | None]] = []
    writer = WriterThread(
        log=logging.getLogger("test.writer"),
        halts_on=(_Halt,),
        on_halt=lambda error, job: halted.append((type(error).__name__, job)),
    )
    writer.start()
    try:
        def moved() -> None:
            raise _Halt("moved")

        with pytest.raises(_Halt):
            writer.call(moved, label="check", job="job_9")
        assert halted == [("_Halt", "job_9")]
        assert writer.halted is not None
        wrote = threading.Event()
        with pytest.raises(WriterStopped, match="halted"):
            writer.call(wrote.set, label="save")
        assert not wrote.is_set()
    finally:
        writer.close()


def test_every_step_runs_inside_the_step_scope_on_the_writer_thread():
    from contextlib import contextmanager

    seen: list[tuple[str, str]] = []

    @contextmanager
    def scope():
        seen.append(("enter", threading.current_thread().name))
        try:
            yield
        finally:
            seen.append(("leave", threading.current_thread().name))

    writer = WriterThread(log=logging.getLogger("test.writer"), step_scope=scope)
    writer.start()
    try:
        writer.call(lambda: None, label="one")
        with pytest.raises(ValueError):
            writer.call(lambda: int("x"), label="two")
    finally:
        writer.close()
    assert seen == [("enter", writer.name), ("leave", writer.name)] * 2


def test_the_writer_thread_does_not_keep_the_process_alive():
    writer = WriterThread(log=logging.getLogger("test.writer"))
    assert writer._thread.daemon is True  # noqa: SLF001
    writer.close()


# ── The I/O pool ─────────────────────────────────────────────────────────────


def test_the_pool_is_four_threads_that_do_not_keep_the_process_alive():
    pool = IOPool()
    try:
        assert pool.size == IO_POOL_SIZE == 4
        assert all(thread.daemon for thread in pool._threads)  # noqa: SLF001
    finally:
        pool.shutdown()


def test_a_fifth_piece_of_work_waits_for_a_free_thread():
    pool = IOPool()
    pool.start()
    release = threading.Event()
    running = threading.Semaphore(0)

    def busy() -> str:
        running.release()
        release.wait(10)
        return threading.current_thread().name

    try:
        first = [pool.submit(busy) for _ in range(4)]
        for _ in range(4):
            assert running.acquire(timeout=5)
        fifth = pool.submit(busy)
        assert not running.acquire(timeout=0.3), "the fifth must wait"
        assert not fifth.running()
        release.set()
        assert fifth.result(5).startswith("pm-ai-io-")
        assert {f.result(5) for f in first} <= {t.name for t in pool._threads}  # noqa: SLF001
    finally:
        release.set()
        pool.shutdown()


def test_pool_work_is_awaited_from_the_loop_without_blocking_it():
    pool = IOPool()
    pool.start()
    release = threading.Event()

    async def scenario() -> list[str]:
        order: list[str] = []
        slow = asyncio.ensure_future(pool.run(lambda: (release.wait(5), "slow")[1]))
        await asyncio.sleep(0.1)
        order.append("loop still answers")
        release.set()
        order.append(await slow)
        return order

    try:
        assert asyncio.run(scenario()) == ["loop still answers", "slow"]
    finally:
        pool.shutdown()


def test_a_step_runs_in_the_handing_threads_context(writer):
    import contextvars

    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")

    def hand() -> str:
        marker.set("the worker's own")
        return writer.call(marker.get, label="read")

    seen: list[str] = []
    worker = threading.Thread(target=lambda: seen.append(hand()))
    worker.start()
    worker.join()
    assert seen == ["the worker's own"]
    assert writer.call(marker.get, label="read") == "unset"


# ── Cancellation never reaches the writer (review, story 4r) ────────────────


def test_an_awaiter_cancelled_mid_step_leaves_the_writer_alive(writer):
    entered, release = threading.Event(), threading.Event()

    def slow() -> str:
        entered.set()
        release.wait(10)
        return "finished"

    async def cancel_mid_step() -> None:
        task = asyncio.ensure_future(writer.call_async(slow, label="slow"))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_mid_step())
    release.set()
    assert writer.call(lambda: "alive", label="after") == "alive"
    assert writer._thread.is_alive()  # noqa: SLF001


def test_an_awaiter_cancelled_before_its_step_started_withdraws_it(writer):
    _, release, handing = _hold(writer)
    ran = threading.Event()

    async def cancel_while_waiting() -> None:
        task = asyncio.ensure_future(writer.call_async(ran.set, label="never"))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_while_waiting())
    release.set()
    handing.join()
    writer.call(lambda: None, label="drain")
    assert not ran.is_set()


def test_call_async_from_the_writer_thread_runs_at_once(writer):
    answered = writer.call(
        lambda: asyncio.run(writer.call_async(lambda: "inline", label="inner")), label="outer"
    )
    assert answered == "inline"


def test_a_halt_whose_recording_fails_still_answers_and_refuses_later_steps():
    def recording_fails(error, job):
        raise RuntimeError("the log is gone")

    writer = WriterThread(
        log=logging.getLogger("test.writer"), halts_on=(_Halt,), on_halt=recording_fails
    )
    writer.start()
    try:
        with pytest.raises(_Halt):
            writer.call(lambda: (_ for _ in ()).throw(_Halt("moved")), label="check")
        with pytest.raises(WriterStopped):
            writer.call(lambda: None, label="later")
        assert writer._thread.is_alive()  # noqa: SLF001
    finally:
        writer.close()
