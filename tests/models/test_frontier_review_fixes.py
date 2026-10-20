"""Story 7c review follow-ups: ledger grammar, the 401 remedy, the table's caps,
the shutdown during a call, and the router's wiring through `build()`.

Every answer comes from an `httpx2.MockTransport`; nothing reaches Anthropic.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import httpx2
import pytest

from pm_ai.app.wiring import build
from pm_ai.core.config import Config
from pm_ai.core.disclosure_ledger import DisclosureLedger, parse_ledger
from pm_ai.domain.disclosure import (
    DisclosureRecord,
    DisclosureStatus,
    MalformedDisclosure,
    render_disclosure,
)
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.model_calls import ScopeFlow
from pm_ai.domain.sanitize import sanitize
from pm_ai.domain.task_classes import TaskClass
from pm_ai.models.frontier.anthropic import MAX_TOKENS, AnthropicFrontier
from pm_ai.models.frontier.models import MODELS, SONNET_TIER
from pm_ai.models.router import ModelRouter
from pm_ai.ports import FrontierFailed, FrontierNotSent

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
KEY = "test-key-not-real"


def _record(**changes):
    base = dict(
        at=NOW,
        task_class="meeting_summary",
        model=SONNET_TIER,
        contributing_scopes=frozenset({PROJECT}),
        input_tokens=None,
        output_tokens=None,
        estimated_cost_usd=None,
        destination=PROJECT,
        call_id="c1",
        status=DisclosureStatus.SENT,
    )
    base.update(changes)
    return DisclosureRecord(**base)


# ── Item 8: the line grammar ─────────────────────────────────────────────────


def test_an_empty_call_id_is_malformed_at_write_and_at_read():
    with pytest.raises(MalformedDisclosure):
        _record(call_id="")
    good = render_disclosure(_record())
    broken = good.replace("call=c1", 'call=""')
    assert broken != good
    with pytest.raises(MalformedDisclosure):
        parse_ledger(broken + "\n")


@pytest.mark.parametrize(
    "changes",
    [{"input_tokens": 5}, {"output_tokens": 5}, {"estimated_cost_usd": 0.1}],
    ids=["input", "output", "cost"],
)
def test_a_sent_line_carrying_usage_or_a_cost_is_malformed(changes):
    with pytest.raises(MalformedDisclosure):
        _record(**changes)
    completed = render_disclosure(
        _record(status=DisclosureStatus.COMPLETED, input_tokens=5, output_tokens=5, estimated_cost_usd=0.1)
    )
    with pytest.raises(MalformedDisclosure):
        parse_ledger(completed.replace("status=completed", "status=sent") + "\n")


# ── Item 11: a refused key names the command ────────────────────────────────


@pytest.mark.parametrize("status", [401, 403])
def test_an_authentication_error_on_a_summary_names_frontier_key(status):
    def handler(request):
        return httpx2.Response(status, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})

    adapter = AnthropicFrontier(transport=httpx2.MockTransport(handler), environ={})
    with pytest.raises(FrontierFailed) as caught:
        adapter.complete(
            model=MODELS[SONNET_TIER], key=KEY, instructions="i",
            external=(sanitize("x"),), shape=None, before_send=lambda: None,
        )
    assert "pm-ai frontier key" in str(caught.value) and KEY not in str(caught.value)


# ── Item 13: the output cap fits every row ──────────────────────────────────


def test_the_output_cap_fits_every_models_maximum():
    for model in MODELS.values():
        assert MAX_TOKENS <= model.max_output_tokens, model.model_id


def test_a_model_whose_maximum_is_below_the_cap_is_refused_before_sending():
    from dataclasses import replace

    sent: list = []
    adapter = AnthropicFrontier(transport=httpx2.MockTransport(lambda r: sent.append(r)), environ={})
    with pytest.raises(FrontierNotSent):
        adapter.complete(
            model=replace(MODELS[SONNET_TIER], max_output_tokens=MAX_TOKENS - 1), key=KEY,
            instructions="i", external=(sanitize("x"),), shape=None, before_send=lambda: None,
        )
    assert sent == []


# ── Item 10: the shutdown during a call keeps the sent line ─────────────────


def test_closing_the_adapter_mid_call_keeps_the_sent_line_and_a_failed_line_follows(tmp_path):
    storage = build(tmp_path, "alpha", now=lambda: NOW, config=Config()).storage
    arrived, release = threading.Event(), threading.Event()

    def blocking(request):
        arrived.set()
        release.wait(10)
        raise httpx2.ReadError("the connection was closed by the shutdown", request=request)

    adapter = AnthropicFrontier(transport=httpx2.MockTransport(blocking), environ={})
    router = ModelRouter(
        local=None, frontier=adapter, storage=storage, key=lambda _s: KEY, clock=lambda: NOW
    )
    raised: list[BaseException] = []

    def call():
        try:
            router.complete(
                task_class=TaskClass.MEETING_SUMMARY,
                instructions="i",
                external=(sanitize("x"),),
                flow=ScopeFlow.of((PROJECT,), PROJECT),
            )
        except BaseException as error:  # noqa: BLE001
            raised.append(error)

    worker = threading.Thread(target=call)
    worker.start()
    assert arrived.wait(10)
    router.close()  # the frontier pool's abort
    release.set()
    worker.join(10)
    assert raised and isinstance(raised[0], FrontierFailed)
    sent, failed = DisclosureLedger(storage).records()
    assert sent.status is DisclosureStatus.SENT
    assert failed.status is DisclosureStatus.FAILED and failed.input_tokens is None
    with pytest.raises(FrontierNotSent):
        router.complete(
            task_class=TaskClass.MEETING_SUMMARY, instructions="i",
            external=(sanitize("x"),), flow=ScopeFlow.of((PROJECT,), PROJECT),
        )


# ── Item 18a: `build()` wires a router whose adapter refuses SDK variables ───


@pytest.mark.parametrize("variable", ["ANTHROPIC_LOG", "ANTHROPIC_CUSTOM_HEADERS"])
def test_builds_router_refuses_a_set_sdk_variable_by_name_and_writes_nothing(
    tmp_path, monkeypatch, variable
):
    from pm_ai.models.frontier import anthropic as frontier
    from tests.slice.test_line_answered_or_acknowledged import _NoKey

    sent: list = []
    monkeypatch.setattr(
        frontier, "_default_transport", lambda: httpx2.MockTransport(lambda r: sent.append(r))
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv(variable, "debug")
    daemon = build(tmp_path, "alpha", keychain=_NoKey(), encryption_disabled=True, now=lambda: NOW)
    assert daemon.router is not None
    with pytest.raises(FrontierNotSent) as caught:
        daemon.router.complete(
            task_class=TaskClass.MEETING_SUMMARY,
            instructions="i",
            external=(sanitize("x"),),
            flow=ScopeFlow.of((PROJECT,), PROJECT),
        )
    assert variable in str(caught.value)
    assert daemon.storage.read_disclosure() == "" and sent == []
