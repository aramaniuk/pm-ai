"""Story 7c — one frontier call has a total deadline, measured from the send.

httpx2's timeout is per phase; a response that trickles bytes forever must
still end at the call's deadline, with its `sent` line kept and a `failed`
line after it, and the adapter usable for the next call. Every answer comes
from an `httpx2.MockTransport`; nothing reaches Anthropic.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx2
import pytest

from pm_ai.app.wiring import build
from pm_ai.core.config import Config
from pm_ai.core.disclosure_ledger import DisclosureLedger
from pm_ai.domain.disclosure import DisclosureStatus
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.model_calls import ScopeFlow
from pm_ai.domain.sanitize import sanitize
from pm_ai.domain.task_classes import TaskClass
from pm_ai.models.frontier.anthropic import AnthropicFrontier
from pm_ai.models.frontier.models import SONNET_TIER
from pm_ai.models.router import ModelRouter
from pm_ai.ports import FrontierFailed, FrontierKeyRefused

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
KEY = "test-key-not-real"
DEADLINE = 0.5


class Trickle(httpx2.SyncByteStream):
    """A body that yields a byte every few hundredths of a second, for ever."""

    def __iter__(self):
        yield b'{"id": "msg_slow", '
        while True:
            time.sleep(0.03)
            yield b" "


def _answer():
    return httpx2.Response(
        200,
        json={
            "id": "msg_ok", "type": "message", "role": "assistant", "model": SONNET_TIER,
            "content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )


class Slow:
    """The first request trickles for ever; later ones answer at once."""

    def __init__(self):
        self.requests = 0

    def __call__(self, request):
        self.requests += 1
        if self.requests == 1:
            return httpx2.Response(200, headers={"content-type": "application/json"}, stream=Trickle())
        return _answer() if request.method == "POST" else httpx2.Response(
            200, json={"data": [], "has_more": False, "first_id": None, "last_id": None}
        )


def _ask(router):
    return router.complete(
        task_class=TaskClass.MEETING_SUMMARY,
        instructions="i",
        external=(sanitize("x"),),
        flow=ScopeFlow.of((PROJECT,), PROJECT),
    )


def test_a_trickling_answer_ends_at_the_total_deadline_and_the_next_call_works(tmp_path):
    storage = build(tmp_path, "alpha", now=lambda: NOW, config=Config()).storage
    slow = Slow()
    adapter = AnthropicFrontier(
        transport=httpx2.MockTransport(slow), environ={}, deadline_seconds=DEADLINE
    )
    router = ModelRouter(local=None, frontier=adapter, storage=storage, key=lambda _s: KEY, clock=lambda: NOW)

    started = time.monotonic()
    with pytest.raises(FrontierFailed) as caught:
        _ask(router)
    took = time.monotonic() - started
    assert DEADLINE <= took < DEADLINE + 2.0, took
    assert f"within {DEADLINE:g} seconds" in str(caught.value) and "total deadline" in str(caught.value)
    sent, failed = DisclosureLedger(storage).records()
    assert sent.status is DisclosureStatus.SENT
    assert failed.status is DisclosureStatus.FAILED and failed.input_tokens is None
    assert failed.call_id == sent.call_id

    answer = _ask(router)  # the adapter is not closed by a deadline
    assert answer.model == SONNET_TIER and slow.requests == 2
    assert [line.status for line in DisclosureLedger(storage).records()][-2:] == [
        DisclosureStatus.SENT,
        DisclosureStatus.COMPLETED,
    ]


def test_the_key_check_has_the_same_total_deadline():
    slow = Slow()
    adapter = AnthropicFrontier(
        transport=httpx2.MockTransport(slow), environ={}, deadline_seconds=DEADLINE
    )
    started = time.monotonic()
    with pytest.raises(FrontierKeyRefused) as caught:
        adapter.check_key(KEY)
    assert time.monotonic() - started < DEADLINE + 2.0
    assert f"within {DEADLINE:g} seconds" in str(caught.value) and KEY not in str(caught.value)
    adapter.check_key(KEY)  # usable again


def test_the_deadline_closes_the_calls_own_transport_when_it_fires():
    from pm_ai.models.frontier.anthropic import _Deadline

    closed: list[bool] = []

    class Inner(httpx2.BaseTransport):
        def handle_request(self, request):  # pragma: no cover — not reached
            raise AssertionError

        def close(self):
            closed.append(True)

    deadline = _Deadline(Inner(), 0.05)
    with deadline:
        time.sleep(0.3)
    assert deadline.fired and closed == [True]
