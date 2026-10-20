"""Story 7e — the review's follow-ups, end to end.

The same rig as `test_member_updates_slice.py` (whose helpers this imports):
`build` over a temporary root, a fake model, `8t`-shaped records, jobs inline
unless a test queues them. No test reaches Ollama or GitLab.
"""

from __future__ import annotations

import builtins
import json
import threading
import types
from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest

from pm_ai.app import entry
from pm_ai.app.pipelines import (
    MATCH_OFFER_LIMIT,
    SummaryJobs,
    SummaryResult,
    match_steps,
    offer_matches,
    run_harvest,
    summarize_meeting,
)
from pm_ai.app.wiring import build
from pm_ai.core.member_updates import ItemKind, LinkKind, UpdateLine, render_update
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.meeting_summaries import (
    RecordedSummary,
    SummaryActionItem,
    SummaryAnswer,
    SummaryStateKind,
)
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.member_ledgers import MatchFence, MatchStateKind
from pm_ai.domain.work import Issue, Member, Section, SectionRead, WorkChange
from pm_ai.ports import ArtifactBusy, LedgerChanged
from pm_ai.surfaces.cli import dispatch as cli
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL
from tests.slice.test_member_updates_slice import (
    IVAN,
    MONDAY,
    NOW,
    PROJECT,
    FakeModel,
    _answer,
    _daemon,
    _ingested,
    _issue,
    _ledger,
    _ledger_bytes,
    _matches,
    _meeting,
    _run,
    _summarised,
    _team_activity,
    _work,
    ANA,
    person,
)

APPLICATION = DataScope(ScopeKind.APPLICATION)
ALIAS_ROW = "- name=x said=x removed=yes at=2026-10-13T12:00:00+00:00"


def _record_work(daemon, *members: Member, issues: Sequence[Issue] = ()) -> None:
    daemon.storage.record_project_work(
        PROJECT,
        WorkChange(
            instance="gitlab:alpha",
            started_at=NOW - timedelta(hours=1),
            members=members,
            issues=tuple(issues),
            sections=tuple(SectionRead(section, True) for section in Section),
        ),
    )


def _waiting_row(daemon, meeting_id: str = "mtg_a", *, start: datetime = MONDAY, attempts: int = 1):
    """A title-match row as failed calls leave it: `attempts` of them (0: unavailable)."""
    state = daemon.storage.record_match(
        PROJECT, meeting_id, meeting_start=start, pending=1,
        reason="the call to the local model failed: Boom", attempted=attempts > 0,
    )
    for _ in range(attempts - 1):
        state = daemon.storage.record_match(
            PROJECT, meeting_id, meeting_start=start, pending=1,
            reason="the call to the local model failed: Boom", attempted=True,
            match=MatchFence(PROJECT, meeting_id, state.version),
        )
    return state


def _with_summary(daemon, meeting: Meeting, *items: tuple[str, str | None, str | None]) -> None:
    daemon.meetings.put(meeting)
    daemon.meetings.put_summary(
        meeting.meeting_id,
        scope=meeting.scope,
        summary=RecordedSummary(
            model="fake:8b",
            requested=False,
            made_on=NOW.date(),
            answer=SummaryAnswer("s", (), tuple(SummaryActionItem(w, o, d) for w, o, d in items)),
        ),
    )


def _holding(daemon, scope: DataScope, artifact: str):
    """A thread holding `artifact`'s claim until released: another holder."""
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with daemon.storage.exclusive(scope=scope, artifact=artifact):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(10)
    return release, holder


# ── 2, 3, 4: `member name` and the scope ─────────────────────────────────────


def test_member_name_refuses_an_inactive_member_or_a_bot_by_any_of_its_names(tmp_path):
    blocked = person(5, "Old Timer", "old.t")
    bot = person(6, "Release Bot", "release-bot", bot=True)
    daemon = _summarised(tmp_path)
    _record_work(daemon, Member(IVAN, "active"), Member(blocked, "blocked"), Member(bot, "active"))
    for named in ("old.t", str(blocked.handle), "Old Timer"):
        code, said = _run(daemon, "member", "name", "--scope", "project:alpha", "Ваня", named)
        assert code == EXIT_REFUSAL and "not an active member" in said, (named, said)
        assert "Ivan Petrov @ivan.p" in said
    for named in ("release-bot", str(bot.handle)):
        code, said = _run(daemon, "member", "name", "--scope", "project:alpha", "Ваня", named)
        assert code == EXIT_REFUSAL and "a bot" in said, (named, said)
    assert daemon.storage.read_aliases() == b""


def test_remove_for_one_meeting_ends_an_unscoped_mapping_there_only(tmp_path):
    daemon = _summarised(tmp_path)
    daemon.model.answers.append(_answer(items=(("fix #41 too", "Ваня", None),)))
    second = _ingested(daemon, _meeting("mtg_b", start=MONDAY + timedelta(hours=3)))
    summarize_meeting(daemon, PROJECT, second.meeting_id)
    assert _run(daemon, "member", "name", "--scope", "project:alpha", "Ваня", "ivan.p")[0] == EXIT_OK

    code, said = _run(
        daemon, "member", "name", "--scope", "project:alpha", "Ваня", "--remove", "--meeting", "mtg_a"
    )
    assert code == EXIT_OK and "in meeting mtg_a" in said, said
    last = daemon.storage.read_aliases().decode("utf-8").splitlines()[-1]
    assert "removed=yes" in last and "meeting=mtg_a" in last
    section = _team_activity(daemon)
    assert "action, unassigned: fix #41 (said" in section
    assert "action for Ivan Petrov: fix #41 too" in section


def test_a_scope_naming_a_project_this_daemon_does_not_know_is_refused_never_a_traceback(tmp_path):
    daemon = _daemon(tmp_path)
    for argv in (
        ("member", "name", "--scope", "project:nowhere", "Ваня", "ivan.p"),
        ("member", "link", "upd_0123456789ab", "--scope", "project:nowhere", "--confirm"),
        ("member", "unmatched", "--scope", "project:nowhere"),
    ):
        code, said = _run(daemon, *argv)
        assert code == EXIT_REFUSAL, (argv, said)
        assert "project:nowhere is not a project this daemon knows" in said
        assert "Traceback" not in said


# ── 12: the appends ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("which", ["updates", "aliases"])
def test_an_append_after_a_torn_last_line_starts_on_a_line_of_its_own(tmp_path, which):
    daemon = _daemon(tmp_path)
    scope, artifact = (PROJECT, "member_updates.md") if which == "updates" else (APPLICATION, "aliases.md")
    path = daemon.storage.paths.resolve(scope, artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"- a hand edit with no newline")
    size = len(path.read_bytes())
    if which == "updates":
        daemon.storage.append_member_updates(["- kind=next"], scope=PROJECT, after=size)
    else:
        daemon.storage.append_alias(ALIAS_ROW, after=size)
    lines = path.read_bytes().split(b"\n")
    assert lines[0] == b"- a hand edit with no newline"
    assert lines[1].startswith(b"- ") and lines[2:] == [b""]


@pytest.mark.parametrize("which", ["updates", "aliases"])
def test_an_append_while_another_holds_the_ledgers_claim_is_refused_busy(tmp_path, which):
    daemon = _daemon(tmp_path)
    scope, artifact = (PROJECT, "member_updates.md") if which == "updates" else (APPLICATION, "aliases.md")
    release, holder = _holding(daemon, scope, artifact)
    try:
        with pytest.raises(ArtifactBusy):
            if which == "updates":
                daemon.storage.append_member_updates(["- kind=x"], scope=PROJECT, after=0)
            else:
                daemon.storage.append_alias(ALIAS_ROW, after=0)
    finally:
        release.set()
        holder.join()
    read = _ledger_bytes(daemon) if which == "updates" else daemon.storage.read_aliases()
    assert read == b""


def test_an_append_by_the_claims_own_holder_is_not_refused(tmp_path):
    daemon = _daemon(tmp_path)
    with daemon.storage.exclusive(scope=APPLICATION, artifact="aliases.md"):
        daemon.storage.append_alias(ALIAS_ROW, after=0)
    assert daemon.storage.read_aliases() == (ALIAS_ROW + "\n").encode()


def test_member_name_while_another_holds_the_alias_table_is_refused(tmp_path):
    daemon = _summarised(tmp_path)
    release, holder = _holding(daemon, APPLICATION, "aliases.md")
    try:
        code, said = _run(daemon, "member", "name", "--scope", "project:alpha", "Ваня", "ivan.p")
    finally:
        release.set()
        holder.join()
    assert code == EXIT_REFUSAL and "Nothing was written" in said
    assert daemon.storage.read_aliases() == b""


def test_member_name_refuses_when_the_table_grew_while_the_pm_was_asked(tmp_path, monkeypatch):
    daemon = _summarised(tmp_path)
    assert _run(daemon, "member", "name", "--scope", "project:alpha", "Ваня", "ivan.p")[0] == EXIT_OK
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)

    def answer(prompt: str) -> str:
        # Another process appends while the question is on the screen.
        daemon.storage.append_alias(ALIAS_ROW, after=len(daemon.storage.read_aliases()))
        return "y"

    monkeypatch.setattr(builtins, "input", answer)
    code, said = _run(daemon, "member", "name", "--scope", "project:alpha", "Ваня", "bo")
    assert code == EXIT_REFUSAL and "changed while you were asked" in said
    assert "handle=" + str(person(3, "Bo Chen", "bo").handle) not in daemon.storage.read_aliases().decode()


# ── 13, 18: doctor ───────────────────────────────────────────────────────────


def test_the_doctor_probe_waiting_ok_old_not_offered_and_given_up_warning(tmp_path):
    daemon = _daemon(tmp_path)
    composed = types.SimpleNamespace(daemon=daemon)
    _waiting_row(daemon, "mtg_a")
    probe = entry._matches_probe(composed)  # type: ignore[arg-type]
    assert probe is not None and probe.health is Health.OK
    assert "1 waiting for the local model's title match: meeting mtg_a in project:alpha." == probe.detail

    _waiting_row(daemon, "mtg_old", start=NOW - timedelta(days=45))
    probe = entry._matches_probe(composed)  # type: ignore[arg-type]
    assert probe.health is Health.OK
    waiting, rest = probe.detail.split(". ", 1)
    assert "mtg_old" not in waiting
    assert "not offered again" in rest and "meeting mtg_old" in rest

    _waiting_row(daemon, "mtg_c", attempts=3)
    probe = entry._matches_probe(composed)  # type: ignore[arg-type]
    assert probe.health is Health.WARNING
    assert "given up after 3 failed calls" in probe.detail and "meeting mtg_c" in probe.detail
    assert "`pm-ai transcript summarize --scope <scope> --meeting <id>`" in probe.remediation


def test_doctor_reports_a_given_up_match_at_warning(tmp_path, monkeypatch):
    from tests.slice.test_line_answered_or_acknowledged import _NoKey

    daemon = _daemon(tmp_path)
    _waiting_row(daemon, "mtg_c", attempts=3)
    # The local model's own probe asks the Ollama server: never here.
    monkeypatch.setattr(entry, "_local_model_probe", lambda composed: Probe("local model", Health.OK, "fine"))
    composed = entry._fresh_composition(daemon, types.SimpleNamespace(selection=None))  # type: ignore[arg-type]
    report = entry._diagnose(_NoKey(), composed, daemon_probe=Probe("daemon", Health.OK, "itself"))
    (probe,) = [p for p in report.probes if p.name == entry.MATCHES_PROBE]
    assert probe.health is Health.WARNING and "meeting mtg_c" in probe.detail


# ── 17: a real harvest ───────────────────────────────────────────────────────


def test_a_real_harvest_offers_the_waiting_match(tmp_path):
    """`_harvest` calls `offer_matches`: the waiting row moves."""
    from tests.slice.test_transcript_path import INSTANCE, _daemon as graph_daemon

    daemon, _ = graph_daemon(tmp_path)
    daemon.model = FakeModel(_matches((1, 41)))  # type: ignore[assignment]
    daemon.summaries = SummaryJobs()
    _work(daemon, _issue(41, IVAN, title="Report export rework"))
    start = datetime(2026, 10, 12, 8, 0, tzinfo=MONDAY.tzinfo)
    _with_summary(daemon, _meeting(start=start), ("rework export", None, None))
    _waiting_row(daemon, start=start)

    run_harvest(daemon, INSTANCE)

    (state,) = daemon.storage.match_records()
    assert state.state is MatchStateKind.MATCHED
    assert daemon.model.classified() == 1
    (line,) = _ledger(daemon).live.values()
    assert line.issue == 41 and line.by is LinkKind.MODEL


# ── 19: through the daemon's request path ────────────────────────────────────


@pytest.fixture
def project_rig(tmp_path):
    import logging

    from pm_ai.app import daemon as daemon_module
    from pm_ai.app.concurrency import IOPool, ModelPool, WriterThread
    from pm_ai.app.prompts import DAEMON_LOGGER, RequestPath
    from pm_ai.storage.service import OperationalStoreStuck, SchemaVersionChanged
    from tests.slice.test_line_answered_or_acknowledged import Rig, _NoKey, _write

    daemon = build(tmp_path, "alpha", keychain=_NoKey(), encryption_disabled=True, now=lambda: NOW)
    _write(daemon.storage.paths.resolve(APPLICATION, "config.toml"), b'display_timezone = "UTC"\n')
    writer = WriterThread(
        log=logging.getLogger(DAEMON_LOGGER),
        step_scope=daemon.storage.claimed_step,
        halts_on=(SchemaVersionChanged, OperationalStoreStuck),
    )
    writer.start()
    writer.call(daemon.storage.adopt_daemon_connection, label="adopt")
    pool = IOPool()
    pool.start()
    model_pool = ModelPool()
    model_pool.start()
    path = RequestPath(
        daemon=daemon, writer=writer, pool=pool, diagnose=lambda: None, bounds=entry.DAEMON_BOUNDS  # type: ignore[arg-type, return-value]
    )
    daemon.writer, daemon.pool, daemon.model_pool = writer, pool, model_pool
    daemon.summaries = daemon_module.summary_jobs(path, model_pool)
    log = daemon_module._RequestLog.attach(daemon)  # noqa: SLF001
    try:
        yield Rig(tmp_path, daemon, writer, pool, path, [])
    finally:
        model_pool.shutdown()
        writer.close()
        pool.shutdown()
        log.detach()
        daemon.storage.close()


def _answered(rig, line: str) -> tuple[int, str]:
    reply = rig.ask(line)
    if reply.status == 202:
        row = rig.wait(reply.body["job_id"], "done", "failed")
        result = json.loads(row.result)
        return result.get("exit", 1), result.get("output", result.get("error", ""))
    return reply.body["exit"], reply.body["output"]


def test_member_link_and_unmatched_through_the_daemons_request_path(project_rig):
    rig = project_rig
    daemon = rig.daemon
    line = UpdateLine(
        kind=ItemKind.ACTION, meeting="mtg_a", start=MONDAY, title="Weekly planning",
        item="prp_x", issue=41, by=LinkKind.MODEL, text="rework export", at=NOW, owner="Ваня",
    )
    rig.writer.call(
        lambda: daemon.storage.append_member_updates([render_update(line)], scope=PROJECT, after=0),
        label="seed",
    )

    code, said = _answered(rig, "member unmatched --scope project:alpha")
    assert code == EXIT_OK, said
    assert line.update in said and '"Ваня": 1 update' in said

    code, said = _answered(rig, f"member link {line.update} --scope project:alpha --confirm")
    assert code == EXIT_OK, said
    assert "confirmed" in said
    text = daemon.storage.read_member_updates(scope=PROJECT).decode("utf-8")
    assert f"- kind=confirmed update={line.update} at=" in text


# ── 8, 9, 21: the step's faults and the row's reason ─────────────────────────


def test_an_unexpected_fault_in_the_step_still_settles_the_summary_and_leaves_the_match_waiting(
    tmp_path, monkeypatch
):
    daemon = _daemon(tmp_path, _answer())
    _work(daemon, _issue(142, ANA))
    _ingested(daemon, _meeting())

    def changed(*args, **kwargs):
        raise LedgerChanged("grew under the step")

    monkeypatch.setattr(daemon.storage, "append_member_updates", changed)
    outcome = summarize_meeting(daemon, PROJECT, "mtg_a")

    assert outcome.result is SummaryResult.SUMMARIZED
    assert daemon.storage.summary_state(PROJECT, "mtg_a").state is SummaryStateKind.SUMMARIZED
    (state,) = daemon.storage.match_records()
    assert state.state is MatchStateKind.WAITING and state.pending == 1
    assert "LedgerChanged" in state.reason


def test_a_fault_with_nothing_said_records_no_phantom_item(tmp_path, monkeypatch):
    daemon = _daemon(tmp_path, _answer(items=()))
    _work(daemon, _issue(142, ANA))
    _ingested(daemon, _meeting())

    def broken(*args, **kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover — a generator, as the real step is

    monkeypatch.setattr("pm_ai.app.pipelines._update_steps", broken)
    assert summarize_meeting(daemon, PROJECT, "mtg_a").result is SummaryResult.SUMMARIZED
    assert daemon.storage.match_records() == ()


def test_a_re_summary_with_nothing_to_ask_clears_a_given_up_row_honestly(tmp_path):
    daemon = _daemon(tmp_path, _answer())
    _work(daemon, _issue(142, ANA))
    _ingested(daemon, _meeting())
    _waiting_row(daemon, attempts=3)
    assert daemon.storage.match_records()[0].state is MatchStateKind.GIVEN_UP

    summarize_meeting(daemon, PROJECT, "mtg_a")

    (state,) = daemon.storage.match_records()
    assert state.state is MatchStateKind.MATCHED and state.pending == 0
    assert "model" not in state.reason
    assert daemon.model.classified() == 0
    assert entry._matches_probe(types.SimpleNamespace(daemon=daemon)) is None  # type: ignore[arg-type]


def test_a_retry_with_no_open_issue_says_so_never_that_the_model_matched(tmp_path):
    daemon = _daemon(tmp_path)
    _work(daemon)  # no issue at all
    _with_summary(daemon, _meeting(), ("rework export", None, None))
    _waiting_row(daemon)
    offer_matches(daemon)
    (state,) = daemon.storage.match_records()
    assert state.state is MatchStateKind.MATCHED
    assert "no open issue" in state.reason and "model" not in state.reason


def test_an_answer_with_too_few_notes_is_an_unreadable_answer(tmp_path, monkeypatch):
    from pm_ai.app import pipelines

    daemon = _daemon(tmp_path, _answer(items=(("rework export", None, None), ("tidy docs", None, None))), "{}")
    _work(daemon, _issue(41, title="Report export rework"))
    _ingested(daemon, _meeting())

    def short(daemon, scope, candidates, issues):
        yield pipelines.Hop.MODEL_IN_TURN
        return (41,)

    monkeypatch.setattr(pipelines, "_classify", short)
    summarize_meeting(daemon, PROJECT, "mtg_a")
    (state,) = daemon.storage.match_records()
    assert state.state is MatchStateKind.WAITING and state.pending == 2
    assert "named 1 notes for the 2 asked" in state.reason
    assert _ledger(daemon).live == {}


# ── 22: the retry's fence and overlap ────────────────────────────────────────


def test_a_retry_whose_row_moved_before_it_ran_writes_nothing(tmp_path):
    daemon = _daemon(tmp_path, _matches((1, 41)))
    _work(daemon, _issue(41, title="Report export rework"))
    _with_summary(daemon, _meeting(), ("rework export", None, None))
    first = _waiting_row(daemon)
    offered = MatchFence(PROJECT, "mtg_a", first.version)
    _waiting_row(daemon)  # a later summary moved the row

    steps = match_steps(daemon, PROJECT, "mtg_a", offered=offered)
    with pytest.raises(StopIteration) as ended:
        next(steps)
    assert ended.value.value is None
    assert _ledger_bytes(daemon) == b"" and daemon.model.classified() == 0


def test_no_retry_while_its_summary_runs_no_summary_while_its_retry_runs_and_the_limit(tmp_path):
    queued: list = []
    daemon = _daemon(tmp_path)
    jobs = SummaryJobs(io=queued.append)
    daemon.summaries = jobs
    _waiting_row(daemon, "mtg_a")

    jobs._in_flight[(PROJECT, "mtg_a")] = "summary_x"  # noqa: SLF001
    offer_matches(daemon)
    assert queued == []
    del jobs._in_flight[(PROJECT, "mtg_a")]  # noqa: SLF001

    jobs._matching.add((PROJECT, "mtg_a"))  # noqa: SLF001
    assert jobs.start(daemon, PROJECT, "mtg_a", offered=None) is False
    jobs._matching.clear()  # noqa: SLF001

    for index in range(MATCH_OFFER_LIMIT + 2):
        _waiting_row(daemon, f"mtg_{index}", start=MONDAY + timedelta(minutes=index))
    offer_matches(daemon)
    assert len(queued) == MATCH_OFFER_LIMIT


# ── 23: an unreadable ledger on the page ─────────────────────────────────────


@pytest.mark.parametrize("which", ["member_updates.md", "aliases.md"])
def test_the_page_renders_and_says_so_when_a_ledger_cannot_be_read(tmp_path, which):
    daemon = _daemon(tmp_path, _answer())
    _work(daemon, _issue(142, ANA))
    _ingested(daemon, _meeting())
    summarize_meeting(daemon, PROJECT, "mtg_a")
    scope = PROJECT if which == "member_updates.md" else APPLICATION
    path = daemon.storage.paths.resolve(scope, which)
    if path.exists():
        path.unlink()
    path.mkdir(parents=True)

    section = _team_activity(daemon)

    if which == "member_updates.md":
        assert "The meeting updates could not be read:" in section
    else:
        assert "The saved alias table could not be read" in section
        assert "action for Ana: fix #142" in section


# ── Andrei's decisions of 2026-10-20 ─────────────────────────────────────────


def test_a_line_linked_by_a_spoken_number_can_be_rejected_and_confirmed_back(tmp_path):
    """Matrix: "Reject a spoken number" — `--reject` works on any line."""
    from pm_ai.core.member_updates import update_id

    daemon = _daemon(tmp_path, _answer(items=(("fix #41", "Ваня", None),)))
    _work(daemon, _issue(41, IVAN))
    _ingested(daemon, _meeting())
    summarize_meeting(daemon, PROJECT, "mtg_a")
    (line,) = _ledger(daemon).live.values()
    assert line.by is LinkKind.NUMBER
    upd = update_id(line.item, 41)
    assert "fix #41" in _team_activity(daemon)

    before = _ledger_bytes(daemon)
    code, said = _run(daemon, "member", "link", upd, "--scope", "project:alpha", "--confirm")
    assert code == EXIT_OK and "already shown as certain" in said
    assert before == _ledger_bytes(daemon), "nothing written"

    code, said = _run(daemon, "member", "link", upd, "--scope", "project:alpha", "--reject")
    assert code == EXIT_OK and "rejected" in said
    assert "fix #41" not in _team_activity(daemon)

    code, said = _run(daemon, "member", "link", upd, "--scope", "project:alpha", "--confirm")
    assert code == EXIT_OK and "confirmed" in said
    assert "fix #41" in _team_activity(daemon) and "(said in the meeting)" in _team_activity(daemon)


def test_the_title_match_lists_the_200_most_recently_updated_and_the_page_says_so(tmp_path):
    """Matrix: "Many open issues" — at most 200 listed; the page counts the rest."""
    from pm_ai.core.member_updates import MAX_LISTED_ISSUES

    issues = [
        _issue(iid, title=f"Task {iid}", updated_at=NOW - timedelta(minutes=iid))
        for iid in range(1, MAX_LISTED_ISSUES + 6)
    ]
    issues.append(_issue(900, title="Secret", confidential=False, state="closed"))
    daemon = _daemon(tmp_path, _answer(items=(("rework the export", None, None),)), _matches((1, 3)))
    _work(daemon, *issues)
    _ingested(daemon, _meeting())
    summarize_meeting(daemon, PROJECT, "mtg_a")

    (call,) = [c for c in daemon.model.calls if c["task_class"].value == "classification"]
    listed = [f.for_model for f in call["external"] if "Task " in f.for_model]
    assert len(listed) == MAX_LISTED_ISSUES
    assert not any(f"Task {iid}" in text.split(": ", 1)[-1] for text in listed for iid in (201, 205))
    assert any(text.endswith("Task 1") for text in listed)
    page = _team_activity(daemon)
    assert "lists only the 200 most recently updated open issues, so 5 open issues were left out" in page


def test_the_page_says_nothing_of_the_cap_when_nothing_was_left_out(tmp_path):
    daemon = _summarised(tmp_path)
    assert "most recently updated open issues" not in _team_activity(daemon)


def test_a_request_told_its_result_whose_settle_then_fails_shows_failed_in_doctor(tmp_path):
    """Accepted limit (Andrei, 2026-10-20): the request was told at the end of
    `7b`'s writes; a failed settle after it is the row `doctor` names as failed."""
    from pm_ai.app.pipelines import request_summary

    daemon = _daemon(tmp_path, _answer(items=(("fix #41", None, None),)))
    _work(daemon, _issue(41))
    _ingested(daemon, _meeting())
    settle = daemon.storage.settle_summary
    broke: list[bool] = []

    def failing(fence, *, state, reason, model=None):
        if state is SummaryStateKind.SUMMARIZED and not broke:
            broke.append(True)
            raise RuntimeError("the disk said no")
        return settle(fence, state=state, reason=reason, model=model)

    daemon.storage.settle_summary = failing  # type: ignore[method-assign]
    told: list = []
    request_summary(daemon, scope=PROJECT, meeting_id="mtg_a", job="job_1", on_done=told.append)

    assert [o.result for o in told] == [SummaryResult.SUMMARIZED], "told once, before the settle"
    state = daemon.storage.summary_state(PROJECT, "mtg_a")
    assert state.state is SummaryStateKind.FAILED
    probe = entry._summaries_probe(types.SimpleNamespace(daemon=daemon))  # type: ignore[arg-type]
    assert probe is not None and probe.health is Health.WARNING
    assert "1 failed: meeting mtg_a" in probe.detail and "RuntimeError" in probe.detail
