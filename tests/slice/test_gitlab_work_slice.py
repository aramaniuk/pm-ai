"""Story 8t — a GitLab harvest through `run_harvest` fills the project's work.

Composed by `build()`, so the connector is the one a daemon runs (with
`reads_work` turned on by composition), and the wire is
`gitlab_fakes.GitLab`: recorded answers, no socket, every URL asserted.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from gitlab_fakes import TOKEN, GitLab, commit, issue, merge_request, person

from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import GitLabEnrolment, build
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.work import Section

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")
HOST = "https://gitlab.example.com"


def composed(tmp_path, gitlab: GitLab, *, now=lambda: NOW):
    daemon = build(tmp_path, "alpha", now=now)
    connector = daemon.connectors["gitlab:alpha"]
    connector.credential = TOKEN
    connector.host = HOST
    connector.transport = gitlab
    assert connector.reads_work, "composition turns the work walk on"
    return daemon


def a_project() -> GitLab:
    gitlab = GitLab()
    gitlab.commits = [commit("abc123", at=NOW - timedelta(hours=2))]
    gitlab.members = [
        person(17, "Dana Smith", "dana", state="active", bot=False),
        person(23, "Lee Chen", "lee", state="active"),
    ]
    gitlab.issues = [
        issue(
            12,
            NOW - timedelta(days=2),
            assignees=[person(17, "Dana Smith", "dana"), person(23, "Lee Chen", "lee")],
            milestone={"title": "Release 3", "due_date": "2026-11-01"},
            labels=["backend", "p1"],
        )
    ]
    gitlab.merge_requests = [merge_request(4, NOW - timedelta(hours=3), source_branch="12-fix")]
    gitlab.related[4] = [issue(12, NOW - timedelta(days=2))]
    return gitlab


def test_one_harvest_fills_members_issues_and_merge_requests(tmp_path):
    daemon = composed(tmp_path, a_project())

    persisted = run_harvest(daemon, "gitlab:alpha")

    assert persisted.persisted == 1, "the commit, as before"
    work = daemon.storage.project_work(ALPHA)
    assert work.instance == "gitlab:alpha"
    assert [(str(m.person.handle), m.person.name, m.person.username) for m in work.members] == [
        ("gitlab:gitlab.example.com:17", "Dana Smith", "dana"),
        ("gitlab:gitlab.example.com:23", "Lee Chen", "lee"),
    ]
    (found,) = work.issues
    assert len(found.assignees) == 2
    assert (found.milestone_title, str(found.milestone_due), found.labels) == (
        "Release 3",
        "2026-11-01",
        ("backend", "p1"),
    )
    (mr,) = work.merge_requests
    assert mr.issue_iids == (12,)
    for which in Section:
        status = work.status(which)
        assert status.complete and status.last_complete == NOW, which

    db = sqlite3.connect(tmp_path / ".pm-ai" / "private" / "operational.db")
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'gitlab_%'"):
        for row in db.execute(f"SELECT * FROM {table}"):
            assert not any(isinstance(v, str) and "the body of" in v for v in row), table


def test_a_second_harvest_asks_from_the_stored_cursors_ascending(tmp_path):
    gitlab = a_project()
    clock = {"at": NOW}
    daemon = composed(tmp_path, gitlab, now=lambda: clock["at"])
    run_harvest(daemon, "gitlab:alpha")
    stored = json.loads(daemon.storage.load_cursor("gitlab:alpha").token)
    gitlab.sent.clear()
    clock["at"] = NOW + timedelta(hours=4)

    run_harvest(daemon, "gitlab:alpha")

    for kind in ("issues", "merge_requests"):
        (query,) = gitlab.asked(kind)
        assert query["sort"] == ["asc"] and query["order_by"] == ["updated_at"]
        assert datetime.fromisoformat(query["updated_after"][0]) == datetime.fromisoformat(
            stored[kind]
        ).replace(microsecond=0)


def test_a_crash_before_the_cursor_is_saved_re_reads_and_replaces_rows_with_themselves(
    tmp_path, monkeypatch
):
    gitlab = a_project()
    daemon = composed(tmp_path, gitlab)
    real_save = daemon.storage.save_cursor

    def crash(*args, **kwargs):
        raise RuntimeError("killed before the position was saved")

    monkeypatch.setattr(daemon.storage, "save_cursor", crash)
    with pytest.raises(RuntimeError):
        run_harvest(daemon, "gitlab:alpha")
    written = daemon.storage.project_work(ALPHA)
    assert written.issues, "the rows were written before the position"

    monkeypatch.setattr(daemon.storage, "save_cursor", real_save)
    gitlab.sent.clear()
    run_harvest(daemon, "gitlab:alpha")

    assert gitlab.asked("issues")[0]["state"] == ["opened"], "re-read from where it was"
    assert daemon.storage.project_work(ALPHA) == written


def test_issues_off_still_reads_the_rest_and_records_commit_coverage(tmp_path):
    from gitlab_fakes import refused

    gitlab = a_project()
    gitlab.refuse["issues"] = refused(404)
    daemon = composed(tmp_path, gitlab)

    run_harvest(daemon, "gitlab:alpha")

    work = daemon.storage.project_work(ALPHA)
    assert not work.status(Section.ISSUES).complete
    assert work.status(Section.MERGE_REQUESTS).complete and work.merge_requests
    assert daemon.storage.harvest_failure("gitlab:alpha") is None
    assert daemon.storage.coverage_windows("gitlab:alpha")


def test_enrolment_places_a_name_in_the_project_it_files_into(tmp_path):
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    enrolment = GitLabEnrolment(daemon.storage, watching=lambda: ("alpha", "beta"))

    assert enrolment.files_into("gitlab:alpha") == "alpha"
    assert enrolment.files_into("gitlab:beta") == "beta"
    assert enrolment.files_into("gitlab:web") is None, "several watched: unplaced"
    assert GitLabEnrolment(daemon.storage, watching=lambda: ("alpha",)).files_into("gitlab:web") == "alpha"


def test_a_first_run_stores_every_open_issue_and_merge_request(tmp_path):
    gitlab = GitLab()
    gitlab.issues = [
        issue(i, NOW - timedelta(days=80 if i == 1 else i)) for i in range(1, 41)
    ]
    gitlab.merge_requests = [merge_request(i, NOW - timedelta(hours=i)) for i in range(1, 7)]
    daemon = composed(tmp_path, gitlab)

    run_harvest(daemon, "gitlab:alpha")

    work = daemon.storage.project_work(ALPHA)
    assert len(work.issues) == 40 and len(work.merge_requests) == 6
    stored = json.loads(daemon.storage.load_cursor("gitlab:alpha").token)
    assert datetime.fromisoformat(stored["issues"]) == NOW - timedelta(days=2)
    assert datetime.fromisoformat(stored["merge_requests"]) == NOW - timedelta(hours=1)


def test_a_429_on_page_two_of_merge_requests_keeps_page_one_and_records_the_failure(tmp_path):
    from gitlab_fakes import refused

    gitlab = a_project()
    gitlab.PER_PAGE = 1
    gitlab.related.clear()
    gitlab.merge_requests = [
        merge_request(4, NOW - timedelta(hours=3)),
        merge_request(5, NOW - timedelta(hours=2)),
    ]

    def before(resource, query):
        if resource == "merge_requests" and query["page"] == "2":
            gitlab.refuse["merge_requests"] = refused(429, **{"Retry-After": "30"})

    gitlab.before = before
    daemon = composed(tmp_path, gitlab)

    run_harvest(daemon, "gitlab:alpha")

    work = daemon.storage.project_work(ALPHA)
    assert [mr.iid for mr in work.merge_requests] == [4]
    assert not work.status(Section.MERGE_REQUESTS).complete
    stored = json.loads(daemon.storage.load_cursor("gitlab:alpha").token)
    assert datetime.fromisoformat(stored["merge_requests"]) == NOW - timedelta(hours=3)
    failure = daemon.storage.harvest_failure("gitlab:alpha")
    assert failure is not None and failure.retryable and "merge requests" in failure.reason
