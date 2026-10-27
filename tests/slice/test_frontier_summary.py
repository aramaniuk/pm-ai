"""Story 7c — a frontier summary of one meeting, on request and disclosed.

One test per row of the story's I/O matrix, against a temporary root. Claude
is a fake frontier adapter or the real one over an `httpx2.MockTransport`;
nothing reaches Anthropic, no key here is real, and no transcript text is real.
Jobs run inline (`SummaryJobs()`), except in the daemon rig at the end, which
runs the call on the frontier pool and the writes on the writer thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone

import httpx2
import pytest

from pm_ai.app import entry
from pm_ai.app.pipelines import (
    Hop,
    SummaryJobs,
    SummaryResult,
    offer_summaries,
    request_summary,
    summary_closing,
)
from pm_ai.app.wiring import build
from pm_ai.core.disclosure_ledger import DisclosureLedger
from pm_ai.core.frontier_key import (
    FRONTIER_KEY,
    FRONTIER_KEY_PROBE,
    frontier_key_probe,
    held_frontier_key,
    seal_frontier_key,
)
from pm_ai.core.connector_enrolment import CREDENTIAL_STORE
from pm_ai.domain.disclosure import DisclosureRecord, DisclosureStatus, render_disclosure
from pm_ai.domain.health import Health
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.meeting_summaries import SummaryMaker, SummaryStateKind
from pm_ai.models.frontier.anthropic import AnthropicFrontier, FrontierReply
from pm_ai.models.frontier.models import SONNET_TIER
from pm_ai.models.router import ModelRouter
from pm_ai.ports import FrontierFailed, FrontierKeyRefused, FrontierModelMismatch, FrontierStopped
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL, dispatch
from tests.slice.test_meeting_summary_slice import (
    NOW,
    PERSONAL,
    PROJECT,
    FakeModel,
    _answer,
    _daemon,
    _ingested,
    _log,
    _meeting,
    _record_text,
    _region,
    _state,
)

KEY = "test-key-not-real"
APPLICATION = DataScope(ScopeKind.APPLICATION)


class FakeFrontier:
    """Writes the `sent` line, then answers with the given text, or raises."""

    def __init__(self, *answers, input_tokens=1200, output_tokens=300):
        self.answers = list(answers)
        self.calls: list[dict] = []
        self.input_tokens, self.output_tokens = input_tokens, output_tokens

    def refused(self):
        return None

    def close(self):
        pass

    def complete(self, *, model, key, instructions, external, shape, before_send):
        before_send()
        self.calls.append(
            {"model": model.model_id, "instructions": instructions, "external": tuple(external), "shape": shape}
        )
        answer = self.answers.pop(0) if self.answers else _answer()
        if isinstance(answer, BaseException):
            raise answer
        return FrontierReply(answer, model.model_id, self.input_tokens, self.output_tokens)


def _claude(daemon, frontier, *, key: str | None = KEY):
    daemon.router = ModelRouter(
        local=daemon.model,
        frontier=frontier,
        storage=daemon.storage,
        key=lambda _storage: key,
        clock=lambda: NOW,
    )
    return frontier


def _ask(daemon, meeting, *, by="claude", again=False):
    ended: list = []
    request_summary(
        daemon, scope=meeting.scope, meeting_id=meeting.meeting_id, job="job_t", by=by,
        on_done=ended.append, again=again,
    )
    (outcome,) = ended
    return outcome


def _lines(daemon):
    return DisclosureLedger(daemon.storage).records()


# ── Request ──────────────────────────────────────────────────────────────────


def test_acceptance_a_project_meeting_by_claude_is_disclosed_written_and_costed(tmp_path):
    """The story's acceptance criterion: a `sent` line before the request and a
    `completed` line with 1,200 / 300 and the table's cost, both naming the
    project as source and destination; the block names Claude and no cost."""
    daemon = _daemon(tmp_path)
    frontier = _claude(daemon, FakeFrontier(_answer()))
    meeting = _ingested(daemon, _meeting())

    outcome = _ask(daemon, meeting)

    assert outcome.result is SummaryResult.SUMMARIZED
    sent, completed = _lines(daemon)
    assert (sent.status, completed.status) == (DisclosureStatus.SENT, DisclosureStatus.COMPLETED)
    assert sent.call_id == completed.call_id
    assert (completed.input_tokens, completed.output_tokens) == (1200, 300)
    assert completed.estimated_cost_usd == pytest.approx(0.0054)
    for line in (sent, completed):
        assert line.contributing_scopes == frozenset({PROJECT}) and line.destination == PROJECT
        assert line.task_class == "meeting_summary" and line.model == SONNET_TIER
    region = _region(_record_text(daemon, meeting))
    assert f"Made by Claude {SONNET_TIER}, on request, on 2026-10-12." in region
    assert "$" not in region and "cost" not in region.lower()
    record = daemon.meetings.get("mtg_a", scope=PROJECT)
    assert record.summary is not None and record.summary.made_by is SummaryMaker.FRONTIER
    # Events and proposals as `7b` writes them.
    assert sum(" decision " in line for line in _log(daemon, PROJECT).splitlines()) == 2
    state = _state(daemon, meeting)
    assert state.state is SummaryStateKind.SUMMARIZED
    assert state.made_by is SummaryMaker.FRONTIER and state.requested and state.model == SONNET_TIER
    # The reply: this call's tokens and cost, and the month's total.
    line, code = summary_closing(outcome)
    assert code == EXIT_OK
    assert "1200 input and 300 output tokens, estimated $0.0054" in line
    assert "This month (2026-10, UTC): $0.01 across 1 call(s), against the $20.00 target" in line
    # One call, `meeting_summary`, the meeting alone, the local model untouched.
    (call,) = frontier.calls
    assert call["model"] == SONNET_TIER and daemon.model.calls == []
    assert call["external"][0].for_model == "Meeting title: Release sync"


def test_the_call_takes_the_frontier_hop_never_the_model_pool(tmp_path):
    daemon = _daemon(tmp_path)
    _claude(daemon, FakeFrontier(_answer()))
    meeting = _ingested(daemon, _meeting())
    hops: list[str] = []

    def pool(name):
        def run(stage):
            hops.append(name)
            stage()

        return run

    daemon.summaries = SummaryJobs(io=pool("io"), model=pool("model"), frontier=pool("frontier"))
    _ask(daemon, meeting)
    assert "frontier" in hops and "model" not in hops[: hops.index("frontier") + 1]
    assert hops.count("frontier") == 1
    assert Hop.FRONTIER.value == "frontier"


def test_a_personal_meetings_frontier_summary_lands_only_in_the_personal_record(tmp_path):
    daemon = _daemon(tmp_path)
    _claude(daemon, FakeFrontier(_answer()))
    meeting = _ingested(daemon, _meeting(scope=PERSONAL))
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.SUMMARIZED
    for line in _lines(daemon):
        assert line.contributing_scopes == frozenset({PERSONAL}) and line.destination == PERSONAL
    assert daemon.meetings.get("mtg_a", scope=PERSONAL).summary is not None
    assert daemon.storage.list_collection(scope=PROJECT, artifact="meetings/") == ()


def test_the_prompt_asks_for_the_meetings_main_language(tmp_path):
    daemon = _daemon(tmp_path)
    frontier = _claude(daemon, FakeFrontier(_answer()))
    vtt = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Ana>Мы выпускаем в пятницу.\n\n"
        "00:00:05.000 --> 00:00:09.000\n<v Bo>Я напишу заметки к четвергу.\n"
    )
    meeting = _ingested(daemon, _meeting(), text=vtt)
    _ask(daemon, meeting)
    assert frontier.calls[0]["instructions"].endswith("Write in Russian.")


# ── Refused, nothing written ─────────────────────────────────────────────────


def test_no_key_is_refused_before_any_line_naming_frontier_key(tmp_path):
    daemon = _daemon(tmp_path)
    frontier = _claude(daemon, FakeFrontier(), key=None)
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.FAILED and "pm-ai frontier key" in outcome.reason
    assert frontier.calls == [] and daemon.storage.read_disclosure() == ""
    assert daemon.meetings.get("mtg_a", scope=PROJECT).summary is None
    assert daemon.model.calls == [], "a frontier failure never falls back to the local model"


@pytest.mark.parametrize("stop", ["max_tokens", "refusal", "pause_turn", "compaction"])
def test_a_stopped_answer_writes_nothing_and_records_its_usage(tmp_path, stop):
    daemon = _daemon(tmp_path)
    stopped = FrontierStopped(
        f"stopped for {stop!r}", stop_reason=stop, input_tokens=900, output_tokens=16000, model=SONNET_TIER
    )
    frontier = _claude(daemon, FakeFrontier(stopped))
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.FAILED and stop in outcome.reason
    assert "OutputCap" not in outcome.reason and "output cap" not in outcome.reason
    assert len(frontier.calls) == 1, "`7b`'s retry never runs on the frontier path"
    assert daemon.meetings.get("mtg_a", scope=PROJECT).summary is None
    _, completed = _lines(daemon)
    assert completed.status is DisclosureStatus.COMPLETED
    assert (completed.input_tokens, completed.output_tokens) == (900, 16000)
    assert _state(daemon, meeting).state is SummaryStateKind.FAILED
    line, _ = summary_closing(outcome)
    assert "900 input and 16000 output tokens" in line


def test_an_answer_from_another_model_is_refused_and_recorded(tmp_path):
    daemon = _daemon(tmp_path)
    mismatch = FrontierModelMismatch(
        "the answer names the model 'claude-opus-5'", sent=True, input_tokens=10, output_tokens=10, model="claude-opus-5"
    )
    _claude(daemon, FakeFrontier(mismatch))
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.FAILED and "claude-opus-5" in outcome.reason
    _, completed = _lines(daemon)
    assert completed.model == "claude-opus-5" and completed.status is DisclosureStatus.COMPLETED
    assert daemon.meetings.get("mtg_a", scope=PROJECT).summary is None


def test_an_invalid_answer_is_refused_once_with_its_line_and_no_retry(tmp_path):
    daemon = _daemon(tmp_path)
    frontier = _claude(daemon, FakeFrontier("not json at all"))
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.FAILED and "not one JSON object" in outcome.reason
    assert len(frontier.calls) == 1
    assert [line.status for line in _lines(daemon)] == [DisclosureStatus.SENT, DisclosureStatus.COMPLETED]
    assert daemon.meetings.get("mtg_a", scope=PROJECT).summary is None
    assert "--by claude` asks again" in outcome.reason


def test_a_failure_after_sending_is_failed_input_unknown_and_named(tmp_path):
    daemon = _daemon(tmp_path)
    _claude(daemon, FakeFrontier(FrontierFailed("the connection dropped mid-answer", sent=True)))
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.FAILED and "dropped" in outcome.reason
    _, failed = _lines(daemon)
    assert failed.status is DisclosureStatus.FAILED and failed.input_tokens is None
    line, _ = summary_closing(outcome)
    assert "unknown" in line and "1 call(s) of unknown cost" in line


def test_an_unwritable_ledger_sends_nothing_and_is_named(tmp_path, monkeypatch):
    daemon = _daemon(tmp_path)
    recorded: list = []

    def handler(request):
        recorded.append(request)
        return httpx2.Response(500)

    _claude(daemon, AnthropicFrontier(transport=httpx2.MockTransport(handler), environ={}))

    def refused(_record, **_kw):
        raise OSError("disclosure.md is read-only")

    monkeypatch.setattr(daemon.storage, "append_disclosure", refused)
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.FAILED and "sent` line could not be written" in outcome.reason
    assert recorded == []


def test_paid_but_not_written_settles_failed_and_says_so(tmp_path, monkeypatch):
    daemon = _daemon(tmp_path)
    _claude(daemon, FakeFrontier(_answer()))
    meeting = _ingested(daemon, _meeting())

    def broken(*_args, **_kwargs):
        raise RuntimeError("the record could not be written")

    monkeypatch.setattr(daemon.meetings, "put_summary", broken)
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.FAILED
    assert "paid for" in outcome.reason and "not written" in outcome.reason
    assert _lines(daemon)[-1].status is DisclosureStatus.COMPLETED
    assert _state(daemon, meeting).state is SummaryStateKind.FAILED


# ── The month ────────────────────────────────────────────────────────────────


def _earlier(daemon, cost):
    daemon.storage.append_disclosure(
        DisclosureRecord(
            at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            task_class="meeting_summary",
            model=SONNET_TIER,
            contributing_scopes=frozenset({PERSONAL}),
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=cost,
            destination=PERSONAL,
        )
    )


def test_a_month_past_twenty_dollars_is_written_as_usual_and_warned(tmp_path):
    daemon = _daemon(tmp_path)
    _claude(daemon, FakeFrontier(_answer()))
    _earlier(daemon, 21.40)
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.SUMMARIZED
    line, code = summary_closing(outcome)
    assert code == EXIT_OK and "Warning: the month is past the $20.00 target" in line
    assert "$21.41" in line


def test_a_malformed_ledger_line_is_named_and_the_summary_written(tmp_path):
    daemon = _daemon(tmp_path)
    _claude(daemon, FakeFrontier(_answer()))
    path = daemon.storage.paths.resolve(APPLICATION, "disclosure.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("- this is not a disclosure line\n")
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting)
    assert outcome.result is SummaryResult.SUMMARIZED
    line, _ = summary_closing(outcome)
    assert "could not be read" in line and "disclosure.md line 1" in line


# ── Local and frontier, never in place of each other ─────────────────────────


def test_a_local_failure_is_reported_and_claude_is_never_called(tmp_path):
    from pm_ai.ports import LocalModelUnavailable

    daemon = _daemon(tmp_path, LocalModelUnavailable("nothing on 127.0.0.1:11434"))
    frontier = _claude(daemon, FakeFrontier())
    meeting = _ingested(daemon, _meeting())
    outcome = _ask(daemon, meeting, by="local")
    assert outcome.result is SummaryResult.WAITING and frontier.calls == []
    assert daemon.storage.read_disclosure() == ""


def test_local_over_a_frontier_summary_replaces_it(tmp_path):
    daemon = _daemon(tmp_path, _answer(summary="The local model's summary."))
    _claude(daemon, FakeFrontier(_answer(summary="Claude's summary.")))
    meeting = _ingested(daemon, _meeting())
    _ask(daemon, meeting)
    # Story 11f: replacing a summary held is said with `--again`.
    _ask(daemon, meeting, by="local", again=True)
    record = daemon.meetings.get("mtg_a", scope=PROJECT)
    assert record.summary.made_by is SummaryMaker.LOCAL
    assert record.summary.answer.summary == "The local model's summary."
    assert _state(daemon, meeting).made_by is SummaryMaker.LOCAL


def test_a_harvest_never_replaces_a_frontier_summary(tmp_path):
    """Every automatic path over a summarised frontier row: a later part's
    re-ingestion re-opening it, an automatic offer, a dropped region's reset,
    then a harvest's offers — none offers it to the local model or replaces it."""
    from pm_ai.app.pipelines import note_summary_input

    daemon = _daemon(tmp_path, _answer(summary="An automatic summary."))
    _claude(daemon, FakeFrontier(_answer(summary="Claude's summary.")))
    meeting = _ingested(daemon, _meeting())
    _ask(daemon, meeting)
    assert _state(daemon, meeting).state is SummaryStateKind.SUMMARIZED

    note_summary_input(daemon, PROJECT, "mtg_a", ingested_now=True)
    assert daemon.storage.offer_summary(
        PROJECT, "mtg_a", meeting_start=meeting.start, reason="a later part", reopen=True
    ) is False
    state = _state(daemon, meeting)
    assert state.state is SummaryStateKind.SUMMARIZED and state.made_by is SummaryMaker.FRONTIER
    offer_summaries(daemon)
    assert daemon.model.calls == []

    daemon.storage.reset_summary(
        PROJECT, "mtg_a", meeting_start=meeting.start, reason="the region was dropped"
    )
    state = _state(daemon, meeting)
    assert state.state is SummaryStateKind.FAILED and state.made_by is SummaryMaker.FRONTIER
    assert state.requested and "--by claude" in state.reason
    offer_summaries(daemon)
    assert daemon.model.calls == []
    assert daemon.meetings.get("mtg_a", scope=PROJECT).summary.made_by is SummaryMaker.FRONTIER


def test_7bs_local_row_is_still_re_opened_by_a_later_part(tmp_path):
    """Unchanged by 7c: an automatic local summary is offered again over the whole meeting."""
    daemon = _daemon(tmp_path, _answer(), _answer())
    meeting = _ingested(daemon, _meeting())
    offer_summaries(daemon)
    assert _state(daemon, meeting).state is SummaryStateKind.SUMMARIZED
    assert daemon.storage.offer_summary(
        PROJECT, "mtg_a", meeting_start=meeting.start, reason="a later part", reopen=True
    ) is True
    assert _state(daemon, meeting).state is SummaryStateKind.WAITING
    daemon.storage.reset_summary(PROJECT, "mtg_a", meeting_start=meeting.start, reason="dropped")
    state = _state(daemon, meeting)
    assert state.state is SummaryStateKind.WAITING and state.made_by is SummaryMaker.LOCAL


def test_a_waiting_request_for_claude_is_never_offered_to_the_local_model(tmp_path):
    daemon = _daemon(tmp_path, _answer())
    meeting = _ingested(daemon, _meeting())
    daemon.storage.request_summary(
        PROJECT, "mtg_a", meeting_start=meeting.start, reason="asked", made_by=SummaryMaker.FRONTIER
    )
    offer_summaries(daemon)
    assert daemon.model.calls == []
    assert daemon.meetings.get("mtg_a", scope=PROJECT).summary is None


def test_a_project_frontier_summary_runs_7es_step_whose_call_stays_local(tmp_path):
    """`7e`'s title match is a local `classification` call, never a frontier one."""
    daemon = _daemon(tmp_path)
    frontier = _claude(daemon, FakeFrontier(_answer()))
    meeting = _ingested(daemon, _meeting())
    _ask(daemon, meeting)
    assert [c["model"] for c in frontier.calls] == [SONNET_TIER]
    assert all(call["task_class"].value != "meeting_summary" for call in daemon.model.calls)


def test_the_request_line_names_claude(tmp_path):
    daemon = _daemon(tmp_path)
    _claude(daemon, FakeFrontier(_answer()))
    meeting = _ingested(daemon, _meeting())
    requested = request_summary(daemon, scope=PROJECT, meeting_id=meeting.meeting_id, job="j", by="claude")
    assert "was asked of Claude" in requested.line


# ── The key ──────────────────────────────────────────────────────────────────


def _plain(tmp_path):
    from tests.slice.test_line_answered_or_acknowledged import _NoKey

    return build(tmp_path, APPLICATION, keychain=_NoKey(), encryption_disabled=True, now=lambda: NOW)


def test_the_key_is_sealed_under_its_own_top_level_key_and_connectors_are_kept(tmp_path):
    daemon = _plain(tmp_path)
    daemon.storage.write_artifact(
        json.dumps({"connectors": {"gitlab:alpha": {"system": "gitlab", "credential": "x"}}}).encode(),
        scope=APPLICATION,
        artifact=CREDENTIAL_STORE,
    )
    assert held_frontier_key(daemon.storage) is None
    assert frontier_key_probe(daemon.storage).health is Health.OK
    seal_frontier_key(daemon.storage, KEY)
    document = json.loads(daemon.storage.read_artifact(scope=APPLICATION, artifact=CREDENTIAL_STORE))
    assert document[FRONTIER_KEY] == {"api_key": KEY}
    assert document["connectors"]["gitlab:alpha"]["credential"] == "x"
    assert held_frontier_key(daemon.storage) == KEY
    probe = frontier_key_probe(daemon.storage)
    assert probe.name == FRONTIER_KEY_PROBE and probe.health is Health.OK and KEY not in str(probe)


def test_doctor_with_no_frontier_key_says_how_to_add_one_and_is_not_a_failure(tmp_path):
    # A missing key is not a failure (Andrei, 2026-10-20), as an unset local model.
    probe = frontier_key_probe(_plain(tmp_path).storage)
    assert probe.health is Health.OK and probe.health.is_healthy
    assert "none held" in probe.detail and "not a failure" in probe.detail
    assert "pm-ai frontier key" in probe.remediation


def test_doctor_with_an_unreadable_credential_store_warns_on_the_frontier_key_line(tmp_path):
    daemon = _plain(tmp_path)
    daemon.storage.write_artifact(b"{not json", scope=APPLICATION, artifact=CREDENTIAL_STORE)
    probe = frontier_key_probe(daemon.storage)
    assert probe.name == FRONTIER_KEY_PROBE and probe.health is Health.WARNING
    assert not probe.health.is_healthy


def test_frontier_key_needs_a_terminal_and_a_daemon(monkeypatch, capsys):
    from pm_ai.surfaces.cli import dispatch as cli

    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)
    code = dispatch(["frontier", "key"], daemon=None, diagnose=lambda: None, probe_connectors=None)  # type: ignore[arg-type, return-value]
    assert code == EXIT_REFUSAL and "terminal" in capsys.readouterr().err

    from pm_ai.surfaces.cli.loopback import NoDaemon

    class Down:
        def reachable(self):
            return NoDaemon("no pm-ai daemon is running; start one with `pm-ai daemon run`.")

        def hand_frontier_key(self, key):
            raise AssertionError("nothing is sent with no daemon")

    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: (_ for _ in ()).throw(AssertionError("not asked")))
    code = dispatch(
        ["frontier", "key"], daemon=None, diagnose=lambda: None, probe_connectors=None, frontier_key=Down()  # type: ignore[arg-type, return-value]
    )
    assert code == EXIT_REFUSAL and "pm-ai daemon run" in capsys.readouterr().err


def test_frontier_key_reads_the_key_hidden_and_hands_it_to_the_daemon(monkeypatch, capsys):
    from pm_ai.surfaces.cli import dispatch as cli
    from pm_ai.surfaces.cli.loopback import Answer

    handed: list[str] = []

    class Up:
        def reachable(self):
            return None

        def hand_frontier_key(self, key):
            handed.append(key)
            return Answer("the Anthropic API key was checked with Anthropic and sealed.\n", 0)

    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: f"  {KEY}\n")
    code = dispatch(["frontier", "key"], daemon=None, diagnose=lambda: None, probe_connectors=None, frontier_key=Up())  # type: ignore[arg-type, return-value]
    out = capsys.readouterr()
    assert code == EXIT_OK and handed == [KEY]
    assert "sealed" in out.out and KEY not in out.out + out.err


def test_frontier_key_is_terminal_bound_and_refused_through_the_daemon():
    from pm_ai.surfaces.cli.dispatch import TERMINAL_BOUND

    assert ("frontier", "key") in TERMINAL_BOUND


def test_frontier_key_from_the_command_line_takes_no_key_argument(capsys):
    code = entry.main(["frontier", "key", KEY])
    said = capsys.readouterr()
    assert code != EXIT_OK and KEY not in said.out and KEY not in said.err


# ── Through the daemon: the frontier pool, the writer, the key endpoint ──────


@pytest.fixture
def frontier_rig(tmp_path):
    from pm_ai.app import daemon as daemon_module
    from pm_ai.app.concurrency import IOPool, ModelPool, WriterThread
    from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
    from pm_ai.storage.service import OperationalStoreStuck, SchemaVersionChanged
    from tests.slice.test_line_answered_or_acknowledged import Rig, _write

    daemon = _plain(tmp_path)
    _write(daemon.storage.paths.resolve(APPLICATION, "config.toml"), b'display_timezone = "UTC"\n')
    daemon.model = FakeModel()  # type: ignore[assignment]
    threads: list[str] = []
    fake = FakeFrontier(_answer())
    real = fake.complete

    def complete(**kwargs):
        threads.append(threading.current_thread().name)
        return real(**kwargs)

    fake.complete = complete  # type: ignore[method-assign]
    daemon.router = ModelRouter(
        local=daemon.model, frontier=fake, storage=daemon.storage,
        key=held_frontier_key, clock=lambda: NOW,
    )
    _ingested(daemon, _meeting(scope=PERSONAL))
    writer = WriterThread(
        log=logging.getLogger(DAEMON_LOGGER),
        step_scope=daemon.storage.claimed_step,
        halts_on=(SchemaVersionChanged, OperationalStoreStuck),
    )
    writer.start()
    writer.call(daemon.storage.adopt_daemon_connection, label="adopt")
    pool = IOPool()
    pool.start()
    model_pool, frontier_pool = ModelPool(), ModelPool(name="pm-ai-frontier")
    model_pool.start()
    frontier_pool.start()
    checks: list[str] = []

    class Check:
        def __init__(self):
            self.refuse: str | None = None

        def check_key(self, key):
            checks.append(threading.current_thread().name)
            if self.refuse:
                raise FrontierKeyRefused(self.refuse)

    check = Check()
    path = RequestPath(
        daemon=daemon, writer=writer, pool=pool, diagnose=lambda: None,  # type: ignore[arg-type, return-value]
        bounds=entry.DAEMON_BOUNDS, key_check=check, key_pool=frontier_pool,
    )
    daemon.writer, daemon.pool, daemon.model_pool = writer, pool, model_pool
    daemon.frontier_pool = frontier_pool
    daemon.summaries = daemon_module.summary_jobs(path, model_pool, frontier_pool)
    log = daemon_module._RequestLog.attach(daemon)  # noqa: SLF001
    try:
        yield Rig(tmp_path, daemon, writer, pool, path, []), threads, check, checks
    finally:
        frontier_pool.shutdown()
        model_pool.shutdown()
        writer.close()
        pool.shutdown()
        log.detach()
        daemon.storage.close()


def test_through_the_daemon_the_key_is_checked_sealed_then_used_on_the_frontier_pool(frontier_rig):
    rig, threads, check, checks = frontier_rig

    reply = rig.ask("transcript summarize --scope personal --meeting mtg_a --by claude")
    row = rig.wait(reply.body["job_id"], "done", "failed")
    result = json.loads(row.result)
    assert result["exit"] == EXIT_REFUSAL and "pm-ai frontier key" in result["output"]

    sealed = asyncio.run(rig.path.frontier_key(KEY))
    assert sealed.body["exit"] == EXIT_OK and KEY not in sealed.body["output"]
    assert checks == ["pm-ai-frontier-1"], "the key check runs on the frontier pool"

    reply = rig.ask("transcript summarize --scope personal --meeting mtg_a --by claude")
    row = rig.wait(reply.body["job_id"], "done", "failed")
    result = json.loads(row.result)
    assert result["exit"] == EXIT_OK, result
    assert "was asked of Claude" in result["output"] and "estimated $0.0054" in result["output"]
    assert threads == ["pm-ai-frontier-1"]


def test_a_refused_key_seals_nothing_and_an_old_key_stays(frontier_rig):
    rig, _, check, _ = frontier_rig
    assert asyncio.run(rig.path.frontier_key(KEY)).body["exit"] == EXIT_OK
    check.refuse = "Anthropic refused the key (401), so it was not sealed; a key already held stays."
    refused = asyncio.run(rig.path.frontier_key("another-test-key"))
    assert refused.body["exit"] == EXIT_REFUSAL and "refused the key" in refused.body["output"]
    assert held_frontier_key(rig.daemon.storage) == KEY
    check.refuse = "https://api.anthropic.com could not be reached to check the key, so it was not sealed."
    down = asyncio.run(rig.path.frontier_key("a-third-test-key"))
    assert "could not be reached" in down.body["output"]
    assert held_frontier_key(rig.daemon.storage) == KEY


def test_the_api_carries_the_key_only_authenticated(tmp_path):
    from starlette.testclient import TestClient

    from pm_ai.surfaces.api.app import FRONTIER_KEY_PATH, Reply, create_app

    got: list[str] = []

    async def handler(key):
        got.append(key)
        return Reply(200, {"inline": True, "output": "sealed\n", "exit": 0})

    client = TestClient(create_app(token="t" * 40, frontier_key=handler))
    assert client.post(FRONTIER_KEY_PATH, json={"key": KEY}).status_code == 401 and got == []
    answer = client.post(FRONTIER_KEY_PATH, json={"key": KEY}, headers={"Authorization": "Bearer " + "t" * 40})
    assert answer.status_code == 200 and got == [KEY] and KEY not in answer.text


# ── Story 7g: the frontier summary gets the same marks ───────────────────────


def test_a_timed_meeting_by_claude_is_sent_the_local_fragments_with_marks(tmp_path):
    """Matrix "Frontier summary": the same fragments with marks, the same instructions."""
    from pm_ai.app.pipelines import _prepare
    from pm_ai.core.meeting_summary import instructions_for

    vtt = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Ana>We ship on Friday.\n\n"
        "00:00:05.000 --> 00:00:09.000\n<v Bo>I will write the notes.\n\n"
        "00:02:05.000 --> 00:02:09.000\n<v Ana>Then we tag it.\n"
    )
    daemon = _daemon(tmp_path)
    frontier = _claude(daemon, FakeFrontier(_answer()))
    meeting = _ingested(daemon, _meeting(), text=vtt)

    _ask(daemon, meeting)

    (call,) = frontier.calls
    texts = [f.for_model for f in call["external"]]
    assert texts[1:] == [
        "[00:00:01] Ana: We ship on Friday.",
        "Bo: I will write the notes.",
        "[00:02:05] Ana: Then we tag it.",
    ]
    prepared = _prepare(daemon, PROJECT, "mtg_a")  # noqa: SLF001
    assert tuple(call["external"]) == prepared.prompt.fragments
    assert call["instructions"] == instructions_for(None, language=prepared.prompt.language)
    assert "[HH:MM:SS]" in call["instructions"]
