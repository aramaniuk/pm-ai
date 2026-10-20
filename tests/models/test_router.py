"""Story 7c — the one router: every class routed, the flow first, the ledger around each frontier call.

The frontier side is a fake adapter or the real one over an `httpx2.MockTransport`;
nothing reaches Anthropic, and no key here is real.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx2
import pytest

from pm_ai.app.wiring import build
from pm_ai.core.config import Config
from pm_ai.core.disclosure_ledger import MONTHLY_TARGET_USD, DisclosureLedger
from pm_ai.domain.disclosure import DisclosureRecord, DisclosureStatus, render_disclosure
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.model_calls import ModelAnswer, ModelSide, ScopeFlow, ScopeFlowRefused
from pm_ai.domain.sanitize import sanitize
from pm_ai.domain.task_classes import TaskClass
from pm_ai.models.frontier.anthropic import AnthropicFrontier, FrontierReply
from pm_ai.models.frontier.models import MODELS, OPUS_TIER, SONNET_TIER
from pm_ai.models.local.ollama import LOCAL_ONLY_CLASSES
from pm_ai.models.router import ROUTES, ModelRouter, Side, route
from pm_ai.ports import (
    FrontierFailed,
    FrontierNotSent,
    FrontierStopped,
    NoLocalModel,
)

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
KEY = "test-key-not-real"
PROJECT = DataScope(ScopeKind.PROJECT, "alpha")
PERSONAL = DataScope(ScopeKind.PERSONAL)


class FakeFrontier:
    """Writes the `sent` line, then answers, or raises what it is given."""

    def __init__(self, answer=None, *, storage=None, refused=None):
        self.answer = answer
        self.models: list[str] = []
        self.storage = storage
        self.ledger_at_send: str | None = None
        self._refused = refused

    def refused(self):
        return self._refused

    def close(self):
        pass

    def complete(self, *, model, key, instructions, external, shape, before_send):
        before_send()
        if self.storage is not None:
            self.ledger_at_send = self.storage.read_disclosure()
        self.models.append(model.model_id)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer or FrontierReply("{}", model.model_id, 1200, 300)


class Local:
    def __init__(self):
        self.calls = []

    def complete(self, *, task_class, instructions, external, flow, shape=None):
        self.calls.append(task_class)
        return ModelAnswer("{}", "fake:8b", True, 1, 1)


@pytest.fixture
def storage(tmp_path):
    return build(tmp_path, "alpha", now=lambda: NOW, config=Config()).storage


def _router(storage, frontier=None, *, local=None, key=KEY, models=MODELS):
    return ModelRouter(
        local=local,
        frontier=frontier,
        storage=storage,
        key=lambda _storage: key,
        clock=lambda: NOW,
        models=models,
    )


def _ask(router, *, task_class=TaskClass.MEETING_SUMMARY, flow=None):
    return router.complete(
        task_class=task_class,
        instructions="pm-ai's own instructions",
        external=(sanitize("Meeting title: Sync"), sanitize("Ana: we ship Friday")),
        flow=flow or ScopeFlow.of((PROJECT,), PROJECT),
    )


def _lines(storage):
    return DisclosureLedger(storage).records()


# ── The table ────────────────────────────────────────────────────────────────


def test_the_table_routes_every_task_class_and_nothing_else():
    assert set(ROUTES) == set(TaskClass)


def test_the_local_side_is_ad15s_five_and_the_local_adapters_own_set():
    local = {c for c, r in ROUTES.items() if r.side is Side.LOCAL}
    assert local == set(LOCAL_ONLY_CLASSES)
    assert all(r.model_id is None for c, r in ROUTES.items() if c in local)


def test_every_frontier_route_has_a_tier_with_a_price_row():
    for task_class, found in ROUTES.items():
        if found.side is Side.FRONTIER:
            assert found.model_id in MODELS, task_class
    assert route(TaskClass.MEETING_SUMMARY).model_id == SONNET_TIER
    assert {route(TaskClass.COACHING).model_id, route(TaskClass.RESEARCH).model_id} == {OPUS_TIER}


def test_the_side_picks_the_pool_and_is_domains_vocabulary():
    assert ModelRouter.side_of(TaskClass.MEETING_SUMMARY) is ModelSide.FRONTIER
    assert ModelRouter.side_of(TaskClass.EXTRACTION) is ModelSide.LOCAL


def test_the_prices_are_the_spines():
    assert (MODELS[SONNET_TIER].input_usd_per_million, MODELS[SONNET_TIER].output_usd_per_million) == (2.0, 10.0)
    assert (MODELS[OPUS_TIER].input_usd_per_million, MODELS[OPUS_TIER].output_usd_per_million) == (4.0, 20.0)


# ── Local classes ────────────────────────────────────────────────────────────


def test_with_no_local_model_local_routes_refuse_and_frontier_routes_work(storage):
    router = _router(storage, FakeFrontier())
    with pytest.raises(NoLocalModel):
        _ask(router, task_class=TaskClass.EXTRACTION)
    assert _ask(router).model == SONNET_TIER


def test_a_local_class_reaches_only_the_local_adapter_and_writes_no_line(storage):
    local, frontier = Local(), FakeFrontier()
    _ask(_router(storage, frontier, local=local), task_class=TaskClass.EXTRACTION)
    assert local.calls == [TaskClass.EXTRACTION] and frontier.models == []
    assert storage.read_disclosure() == ""


# ── The acceptance criterion ─────────────────────────────────────────────────


def test_acceptance_a_sent_line_before_and_a_completed_line_with_the_tables_cost(storage):
    frontier = FakeFrontier(storage=storage)
    answer = _ask(_router(storage, frontier))
    assert frontier.ledger_at_send is not None and "status=sent" in frontier.ledger_at_send
    sent, completed = _lines(storage)
    assert sent.status is DisclosureStatus.SENT and completed.status is DisclosureStatus.COMPLETED
    assert sent.call_id == completed.call_id == answer.call_id
    assert (sent.input_tokens, sent.output_tokens, sent.estimated_cost_usd) == (None, None, None)
    assert (completed.input_tokens, completed.output_tokens) == (1200, 300)
    assert completed.estimated_cost_usd == pytest.approx(1200 * 2 / 1e6 + 300 * 10 / 1e6)
    for line in (sent, completed):
        assert line.contributing_scopes == frozenset({PROJECT}) and line.destination == PROJECT
        assert line.task_class == "meeting_summary" and line.model == SONNET_TIER
    assert answer.estimated_cost_usd == completed.estimated_cost_usd and not answer.local
    assert KEY not in storage.read_disclosure()
    assert "we ship" not in storage.read_disclosure()


def test_a_personal_meetings_lines_name_personal_to_personal(storage):
    _ask(_router(storage, FakeFrontier()), flow=ScopeFlow.of((PERSONAL,), PERSONAL))
    for line in _lines(storage):
        assert line.contributing_scopes == frozenset({PERSONAL}) and line.destination == PERSONAL


def test_every_call_has_a_new_call_id(storage):
    router = _router(storage, FakeFrontier())
    first, second = _ask(router), _ask(router)
    assert first.call_id != second.call_id


# ── Refused before any line ──────────────────────────────────────────────────


def test_personal_into_a_project_is_refused_before_any_line(storage):
    frontier = FakeFrontier()
    with pytest.raises(ScopeFlowRefused):
        _ask(_router(storage, frontier), flow=ScopeFlow.of((PERSONAL,), PROJECT))
    assert frontier.models == [] and storage.read_disclosure() == ""


def test_no_key_is_refused_before_any_line_naming_frontier_key(storage):
    frontier = FakeFrontier()
    with pytest.raises(FrontierNotSent) as caught:
        _ask(_router(storage, frontier, key=None))
    assert "pm-ai frontier key" in str(caught.value)
    assert frontier.models == [] and storage.read_disclosure() == ""


def test_an_unpriced_model_is_refused_before_any_line(storage):
    frontier = FakeFrontier()
    with pytest.raises(FrontierNotSent) as caught:
        _ask(_router(storage, frontier, models={}))
    assert SONNET_TIER in str(caught.value)
    assert frontier.models == [] and storage.read_disclosure() == ""


def test_an_sdk_variable_is_refused_before_any_line(storage):
    frontier = FakeFrontier(refused="ANTHROPIC_LOG is set")
    with pytest.raises(FrontierNotSent):
        _ask(_router(storage, frontier))
    assert frontier.models == [] and storage.read_disclosure() == ""


def test_too_long_through_the_real_adapter_writes_no_line(storage):
    from dataclasses import replace

    sent: list = []

    def handler(request):
        sent.append(request)
        return httpx2.Response(500)

    tiny = {SONNET_TIER: replace(MODELS[SONNET_TIER], context_window=10)}
    frontier = AnthropicFrontier(transport=httpx2.MockTransport(handler), environ={})
    with pytest.raises(FrontierNotSent):
        _ask(_router(storage, frontier, models=tiny))
    assert sent == [] and storage.read_disclosure() == ""


# ── After sending ────────────────────────────────────────────────────────────


def test_a_stopped_answer_writes_a_completed_line_with_its_usage(storage):
    stopped = FrontierStopped("stopped", stop_reason="max_tokens", input_tokens=900, output_tokens=16000, model=SONNET_TIER)
    with pytest.raises(FrontierStopped) as caught:
        _ask(_router(storage, FakeFrontier(stopped)))
    sent, completed = _lines(storage)
    assert completed.status is DisclosureStatus.COMPLETED
    assert (completed.input_tokens, completed.output_tokens) == (900, 16000)
    assert caught.value.call_id == sent.call_id and caught.value.cost_usd == completed.estimated_cost_usd


def test_a_failure_after_sending_records_failed_input_unknown(storage):
    with pytest.raises(FrontierFailed):
        _ask(_router(storage, FakeFrontier(FrontierFailed("dropped", sent=True))))
    _, failed = _lines(storage)
    assert failed.status is DisclosureStatus.FAILED
    assert (failed.input_tokens, failed.output_tokens, failed.estimated_cost_usd) == (None, None, None)
    assert "input_tokens=unknown" in storage.read_disclosure()


def test_an_abandoned_call_keeps_only_its_sent_line(storage):
    class Abandoned(Exception):
        pass

    with pytest.raises(Abandoned):
        _ask(_router(storage, FakeFrontier(Abandoned())))
    (only,) = _lines(storage)
    assert only.status is DisclosureStatus.SENT


def test_the_ledger_line_after_a_call_that_cannot_be_written_never_hides_the_answer(storage):
    class Flaky:
        def __init__(self, inner):
            self.inner, self.calls = inner, 0

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def append_disclosure(self, record):
            self.calls += 1
            if self.calls > 1:
                raise OSError("full")
            self.inner.append_disclosure(record)

    flaky = Flaky(storage)
    answer = _ask(_router(flaky, FakeFrontier()))
    assert answer.model == SONNET_TIER
    (only,) = _lines(storage)
    assert only.status is DisclosureStatus.SENT


# ── The month's total ────────────────────────────────────────────────────────


def _line(at, status, call, *, usage=(10, 10), cost=1.0):
    return render_disclosure(
        DisclosureRecord(
            at=at,
            task_class="meeting_summary",
            model=SONNET_TIER,
            contributing_scopes=frozenset({PROJECT}),
            input_tokens=None if usage is None else usage[0],
            output_tokens=None if usage is None else usage[1],
            estimated_cost_usd=cost,
            destination=PROJECT,
            call_id=call,
            status=status,
        )
    ) + "\n"


class Text:
    def __init__(self, text):
        self.text = text

    def read_disclosure(self):
        return self.text


def test_the_total_counts_completed_calls_and_states_unfinished_ones_as_unknown():
    day = datetime(2026, 10, 5, tzinfo=timezone.utc)
    S, C, F = DisclosureStatus.SENT, DisclosureStatus.COMPLETED, DisclosureStatus.FAILED
    text = (
        _line(day, S, "a", usage=None, cost=None) + _line(day, C, "a", cost=2.0)
        + _line(day, S, "b", usage=None, cost=None)  # sent, never finished
        + _line(day, S, "c", usage=None, cost=None) + _line(day, F, "c", usage=None, cost=None)
        + _line(day, S, "d", usage=None, cost=None) + _line(day, F, "d", cost=0.5)
        + render_disclosure(DisclosureRecord(day, "coaching", OPUS_TIER, frozenset({PERSONAL}), 5, 5, 1.25)) + "\n"
    )
    total = DisclosureLedger(Text(text)).monthly_total(2026, 10, target_usd=MONTHLY_TARGET_USD)
    assert total.cost_usd == pytest.approx(3.75) and total.records == 3
    assert total.unknown == 2
    assert total.breached is None, "within the target on the known total, with unknowns: not told"


def test_a_call_belongs_to_the_month_of_its_sent_line():
    S, C = DisclosureStatus.SENT, DisclosureStatus.COMPLETED
    late = datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)
    text = _line(late, S, "x", usage=None, cost=None) + _line(late + timedelta(minutes=3), C, "x", cost=4.0)
    ledger = DisclosureLedger(Text(text))
    assert ledger.monthly_total(2026, 9).cost_usd == 4.0
    assert ledger.monthly_total(2026, 10).cost_usd == 0.0


def test_past_the_target_is_a_warning_only():
    day = datetime(2026, 10, 5, tzinfo=timezone.utc)
    text = _line(day, DisclosureStatus.COMPLETED, "big", cost=21.40)
    total = DisclosureLedger(Text(text)).monthly_total(2026, 10, target_usd=MONTHLY_TARGET_USD)
    assert total.breached is True and total.cost_usd == pytest.approx(21.40)


def test_a_malformed_line_is_named_when_totalling_past_it():
    day = datetime(2026, 10, 5, tzinfo=timezone.utc)
    text = _line(day, DisclosureStatus.COMPLETED, "ok", cost=1.0) + "not a line\n"
    total = DisclosureLedger(Text(text)).monthly_total(2026, 10, skip_malformed=True)
    assert total.cost_usd == 1.0 and len(total.malformed) == 1 and "line 2" in total.malformed[0]


def test_a_pre_7c_line_cannot_leave_its_cost_unknown():
    from pm_ai.domain.disclosure import MalformedDisclosure

    with pytest.raises(MalformedDisclosure):
        DisclosureRecord(NOW, "t", "m", frozenset({PERSONAL}), None, None, None)
