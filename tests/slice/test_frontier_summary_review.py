"""Story 7c review follow-ups, at the pipeline, the daemon and the CLI.

Claude is a fake frontier adapter; nothing reaches Anthropic and no key here is real.
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

import pytest

from pm_ai.app import entry
from pm_ai.app.pipelines import SummaryJobs, SummaryResult, request_summary, summary_closing
from pm_ai.core.disclosure_ledger import DisclosureLedger
from pm_ai.domain.disclosure import DisclosureRecord, DisclosureStatus
from pm_ai.domain.meeting_summaries import SummaryLeaseLost, SummaryMaker, SummaryStateKind
from pm_ai.models.frontier.models import SONNET_TIER
from pm_ai.models.router import ModelRouter
from pm_ai.ports import FrontierStopped
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL
from tests.slice.test_frontier_summary import (
    KEY,
    FakeFrontier,
    _ask,
    _claude,
    _lines,
    _plain,
    frontier_rig,  # noqa: F401 — fixture
)
from tests.slice.test_meeting_summary_slice import (
    PROJECT,
    _answer,
    _daemon,
    _ingested,
    _meeting,
    _state,
)


# ── Item 1: an unexpected error settles the row, never leaves it waiting ─────


def test_an_unexpected_error_in_the_frontier_call_settles_failed_naming_its_type(tmp_path):
    daemon = _daemon(tmp_path)

    class Broken(FakeFrontier):
        def complete(self, **kwargs):
            kwargs["before_send"]()
            raise KeyError("something pm-ai did not expect")

    _claude(daemon, Broken())
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.FAILED and "KeyError" in outcome.reason
    state = _state(daemon, meeting)
    assert state.state is SummaryStateKind.FAILED and state.made_by is SummaryMaker.FRONTIER
    line, code = summary_closing(outcome)
    assert code == EXIT_REFUSAL and "KeyError" in line
    (sent,) = _lines(daemon)
    assert sent.status is DisclosureStatus.SENT, "the abandoned call keeps its sent line"


# ── Item 2: a lost lease after a paid call still reports what it cost ───────


def test_a_lost_lease_after_a_paid_refusal_still_reports_the_spend(tmp_path):
    daemon = _daemon(tmp_path)
    stopped = FrontierStopped(
        "stopped", stop_reason="refusal", input_tokens=700, output_tokens=40, model=SONNET_TIER
    )
    _claude(daemon, FakeFrontier(stopped))
    meeting = _ingested(daemon, _meeting())
    real = daemon.storage.settle_summary

    def moved_on(fence, **kwargs):
        if kwargs.get("state") is SummaryStateKind.FAILED:
            raise SummaryLeaseLost("a newer request moved the row")
        return real(fence, **kwargs)

    daemon.storage.settle_summary = moved_on  # type: ignore[method-assign]
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.STOPPED
    assert "700 input and 40 output tokens" in outcome.spend


# ── Item 3: the month is the sent line's ────────────────────────────────────


def test_the_reply_totals_the_month_of_the_sent_line_not_the_reply(tmp_path):
    daemon = _daemon(tmp_path)
    frontier = FakeFrontier(_answer())
    late = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    daemon.router = ModelRouter(
        local=daemon.model, frontier=frontier, storage=daemon.storage,
        key=lambda _s: KEY, clock=lambda: late,
    )
    daemon.storage.append_disclosure(
        DisclosureRecord(
            at=datetime(2026, 10, 2, tzinfo=timezone.utc), task_class="meeting_summary",
            model=SONNET_TIER, contributing_scopes=frozenset({PROJECT}), input_tokens=1,
            output_tokens=1, estimated_cost_usd=5.0, destination=PROJECT,
        )
    )
    meeting = _ingested(daemon, _meeting())
    daemon.clock = lambda: datetime(2026, 11, 1, 0, 0, 5, tzinfo=timezone.utc)
    outcome = _ask(daemon, meeting)
    assert "This month (2026-10, UTC): $5.01 across 2 call(s)" in outcome.spend


# ── Item 4: unknown calls never let the reply claim the month is within target ─


def test_with_unknown_calls_the_reply_does_not_claim_the_month_is_within_target(tmp_path):
    daemon = _daemon(tmp_path)
    _claude(daemon, FakeFrontier(_answer()))
    daemon.storage.append_disclosure(
        DisclosureRecord(
            at=datetime(2026, 10, 2, tzinfo=timezone.utc), task_class="meeting_summary",
            model=SONNET_TIER, contributing_scopes=frozenset({PROJECT}), input_tokens=None,
            output_tokens=None, estimated_cost_usd=None, destination=PROJECT,
            call_id="earlier", status=DisclosureStatus.SENT,
        )
    )
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting)
    total = DisclosureLedger(daemon.storage).monthly_total(2026, 10, target_usd=20.0)
    assert total.breached is None and total.unknown == 1
    assert "cannot be told" in outcome.spend and "Warning" not in outcome.spend


# ── Item 7: the key's seal is checked locally before Anthropic is asked ─────


def test_no_master_key_refuses_the_key_before_any_request(tmp_path):
    from pm_ai.app.concurrency import IOPool, ModelPool
    from pm_ai.app.prompts import RequestPath
    from pm_ai.app.wiring import build
    from tests.slice.test_line_answered_or_acknowledged import _NoKey

    daemon = build(tmp_path, "alpha", keychain=_NoKey(), encryption_disabled=False)
    asked: list[str] = []

    class Check:
        def check_key(self, key):
            asked.append(key)

    pool, frontier_pool = IOPool(), ModelPool(name="pm-ai-frontier")
    pool.start()
    frontier_pool.start()
    try:
        path = RequestPath(
            daemon=daemon, writer=None, pool=pool, diagnose=lambda: None,  # type: ignore[arg-type, return-value]
            key_check=Check(), key_pool=frontier_pool,
        )
        reply = asyncio.run(path.frontier_key(KEY))
    finally:
        frontier_pool.shutdown()
        pool.shutdown()
    assert reply.body["exit"] == EXIT_REFUSAL and "KeyNotFound" in reply.body["output"]
    assert "none was asked of Anthropic" in reply.body["output"] and asked == []


# ── Item 9: no frontier pool is a named wiring error, never this thread ─────


def test_summary_jobs_without_a_frontier_pool_refuse_the_hop_by_name(tmp_path):
    from pm_ai.app import daemon as daemon_module

    daemon = _daemon(tmp_path)
    frontier = _claude(daemon, FakeFrontier(_answer()))
    requests = types.SimpleNamespace(
        writer=None, pool=types.SimpleNamespace(submit=lambda stage: stage()), fresh=lambda: daemon
    )
    model_pool = types.SimpleNamespace(submit=lambda stage, ahead=False: stage(), stopping=False)
    daemon.summaries = daemon_module.summary_jobs(requests, model_pool, None)  # type: ignore[arg-type]
    meeting = _ingested(daemon, _meeting())
    ended: list = []
    request_summary(daemon, scope=PROJECT, meeting_id=meeting.meeting_id, job="j", by="claude", on_done=ended.append)
    (result,) = ended
    assert isinstance(result, daemon_module.FrontierPoolMissing)
    assert "frontier pool" in str(result) and frontier.calls == []
    assert isinstance(daemon.summaries, SummaryJobs)


# ── Item 14: with encryption off the reply says unencrypted ─────────────────


def test_the_key_with_encryption_off_is_said_stored_unencrypted(frontier_rig):  # noqa: F811
    rig, _, _, _ = frontier_rig
    reply = asyncio.run(rig.path.frontier_key(KEY))
    assert reply.body["exit"] == EXIT_OK
    assert "stored UNENCRYPTED" in reply.body["output"] and "sealed in" not in reply.body["output"]


# ── Item 18c: `pm-ai frontier key` hands the hidden key to the loopback client ─


def test_frontier_key_through_main_reaches_the_loopback_client(monkeypatch, capsys, tmp_path):
    from pm_ai.surfaces.cli import dispatch as cli

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from pm_ai.surfaces.cli.loopback import Answer

    handed: list[str] = []

    class Recorder:
        def reachable(self):
            return None

        def hand_frontier_key(self, key):
            handed.append(key)
            return Answer("checked and sealed.\n", 0)

        def send(self, line):  # pragma: no cover — never a line
            raise AssertionError("the key is never sent as a line")

    monkeypatch.setattr(entry, "_loopback", lambda: Recorder())
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: KEY)
    assert entry.main(["frontier", "key"]) == EXIT_OK
    assert handed == [KEY]
    said = capsys.readouterr()
    assert KEY not in said.out + said.err


# ── Item 18d: the served daemon's app is given the key handler ──────────────


def test_the_served_daemon_wires_the_frontier_key_endpoint(tmp_path, monkeypatch):
    import socket

    from starlette.testclient import TestClient

    from pm_ai.app import daemon as daemon_module
    from pm_ai.core.config import Config
    from pm_ai.domain.health import Report
    from pm_ai.surfaces.api.app import FRONTIER_KEY_PATH, create_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    daemon = _plain(tmp_path)
    daemon.config = Config(daemon_port=port)
    seen: dict = {}

    class Stop(Exception):
        pass

    def recorded(**kwargs):
        seen.update(kwargs)
        raise Stop

    monkeypatch.setattr(daemon_module, "create_app", recorded)
    with pytest.raises(Stop):
        daemon_module.serve(daemon, probes=lambda: Report(()))
    assert seen.get("frontier_key") is not None
    app = create_app(token="t" * 43, frontier_key=seen["frontier_key"])
    answer = TestClient(app).post(
        FRONTIER_KEY_PATH, json={"key": "   "}, headers={"Authorization": "Bearer " + "t" * 43}
    )
    assert answer.status_code != 503 and answer.json()["exit"] == EXIT_REFUSAL


# ── Item 18e: `doctor` prints the frontier key line; none held is not a failure ─────────────────────────


def test_doctor_prints_the_frontier_key_line_none_held_then_held_and_stays_healthy(
    home, tmp_path, keychain, operator, installed, capsys  # noqa: F811
):
    from tests.slice.test_first_run import answers_for, hold_a_frontier_key, setup

    operator.script(*answers_for(tmp_path / "alpha"))
    setup()
    capsys.readouterr()
    assert entry.main(["doctor"]) == EXIT_OK  # Andrei, 2026-10-20: not a failure
    printed = capsys.readouterr().out
    line = next(line for line in printed.splitlines() if "] frontier key:" in line)
    assert "[     ok]" in line and "none held" in line and "not a failure" in line
    assert "pm-ai frontier key" in printed.split("] frontier key:")[1].split("\n[")[0]
    hold_a_frontier_key(keychain)
    entry.main(["doctor"])
    printed = capsys.readouterr().out
    line = next(line for line in printed.splitlines() if "] frontier key:" in line)
    assert "[     ok]" in line and KEY not in printed


from tests.slice.test_first_run import home, installed, keychain, operator  # noqa: E402, F401 — fixtures
