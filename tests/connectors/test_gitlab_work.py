"""Story 8t — a GitLab harvest reads the project's members, issues and merge requests.

Every row runs the real connector over a recorded-answer transport
(`gitlab_fakes.GitLab`, which filters and pages the way the connector asks
GitLab to): no socket, and the URLs that went out are asserted. The merge into
what is stored is `tests/core/test_gitlab_work_merge.py`'s; a harvest through
`run_harvest` is `tests/slice/test_gitlab_work_slice.py`'s.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from gitlab_fakes import (
    PROJECT_ID,
    TOKEN,
    GitLab,
    commit,
    issue,
    merge_request,
    person,
    refused,
    stamp,
)

from pm_ai.connectors.gitlab import GitLabConnectorAdapter
from pm_ai.connectors.gitlab.work import FIRST_RUN_SLACK
from pm_ai.domain.handles import parse_person_handle
from pm_ai.domain.harvest import Cursor, HarvestOutcome
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.work import CONFIDENTIAL_ISSUE_TITLE, Section, is_open

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
SCOPE = DataScope(ScopeKind.PROJECT, "alpha")
HOST = "https://gitlab.example.com"


class Clock:
    """A clock a test moves by hand."""

    def __init__(self, at: datetime = NOW) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def adapter(gitlab: GitLab, *, host: str = HOST, now=lambda: NOW, **settings) -> GitLabConnectorAdapter:
    return GitLabConnectorAdapter(
        instance="gitlab:alpha",
        project="acme/web",
        scope=SCOPE,
        host=host,
        now=now,
        credential=TOKEN,
        transport=gitlab,
        **{"reads_work": True, **settings},
    )


def positions(cursor: Cursor) -> dict:
    return json.loads(cursor.token)


def at(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


def section(result, which: Section):
    return next(read for read in result.work.sections if read.section is which)


# ── First runs ───────────────────────────────────────────────────────────────


def test_a_first_run_reads_every_open_issue_of_any_age_and_every_open_merge_request():
    gitlab = GitLab()
    gitlab.issues = [issue(i, NOW - timedelta(days=80 if i == 1 else 40 - i)) for i in range(1, 41)]
    gitlab.issues.append(issue(99, NOW - timedelta(days=1), state="closed"))
    gitlab.merge_requests = [merge_request(i, NOW - timedelta(hours=i)) for i in range(1, 7)]

    result = adapter(gitlab).harvest(Cursor())

    assert result.failure is None
    assert sorted(i.iid for i in result.work.issues) == list(range(1, 41)), "nothing closed"
    assert len(result.work.merge_requests) == 6
    (first,) = gitlab.asked("issues")
    assert first["state"] == ["opened"] and "updated_after" not in first
    assert first["order_by"] == ["updated_at"] and first["sort"] == ["asc"]
    assert first["per_page"] == ["100"]
    assert gitlab.asked("merge_requests")[0]["state"] == ["opened"]
    stored = positions(result.cursor)
    assert at(stored["issues"]) == max(i.updated_at for i in result.work.issues)
    assert at(stored["merge_requests"]) == NOW - timedelta(hours=1)
    assert result.work.open_issue_iids == frozenset(range(1, 41))


def test_a_first_run_with_nothing_open_sets_each_cursor_five_minutes_before_its_start():
    result = adapter(GitLab()).harvest(Cursor())

    stored = positions(result.cursor)
    assert at(stored["issues"]) == NOW - FIRST_RUN_SLACK
    assert at(stored["merge_requests"]) == NOW - FIRST_RUN_SLACK
    assert result.outcome is HarvestOutcome.HARVESTED, "a change counts as harvested"
    assert result.coverage is not None, "the commit walk's window stands beside it"


def test_a_stored_commit_only_cursor_still_reads_and_the_work_starts_on_a_first_run():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW - timedelta(days=3))]

    result = adapter(gitlab).harvest(Cursor(b"2026-10-17T08:00:00Z"))

    assert result.failure is None
    assert gitlab.asked("commits")[0]["since"] == ["2026-09-17T08:00:00Z"]
    assert gitlab.asked("issues")[0]["state"] == ["opened"]
    assert positions(result.cursor)["commits"] == "2026-10-17T08:00:00Z"


def test_a_second_run_asks_from_each_stored_cursor_ascending():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW - timedelta(days=3))]
    gitlab.merge_requests = [merge_request(2, NOW - timedelta(days=2))]
    first = adapter(gitlab).harvest(Cursor())
    gitlab.sent.clear()

    adapter(gitlab).harvest(first.cursor)

    stored = positions(first.cursor)
    for kind in ("issues", "merge_requests"):
        query = gitlab.asked(kind)[-1]
        assert query["state"] == ["all"] and query["sort"] == ["asc"]
        assert at(query["updated_after"][0]) == at(stored[kind]).replace(microsecond=0)


# ── People ───────────────────────────────────────────────────────────────────


def test_an_assignee_is_a_gitlab_handle_on_the_server_with_name_and_username():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW, assignees=[person(17, "Dana Smith", "dana")])]

    (read,) = adapter(gitlab).harvest(Cursor()).work.issues

    (dana,) = read.assignees
    assert str(dana.handle) == "gitlab:gitlab.example.com:17"
    assert (dana.name, dana.username) == ("Dana Smith", "dana")


def test_a_server_on_a_port_keeps_it_in_the_handle_which_parses_at_the_last_colon():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW, assignees=[person(17)])]

    (read,) = adapter(gitlab, host="https://GitLab.example.com:8443").harvest(Cursor()).work.issues

    assert str(read.assignees[0].handle) == "gitlab:gitlab.example.com:8443:17"
    assert parse_person_handle("gitlab:gitlab.example.com:8443:17") == read.assignees[0].handle


@pytest.mark.parametrize("user_id", ["x17", True, None, "17", -3])
def test_an_id_that_is_not_a_number_gives_no_handle_and_keeps_the_names(user_id):
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW, assignees=[person(user_id, "Dana Smith", "dana")])]

    (read,) = adapter(gitlab).harvest(Cursor()).work.issues

    (dana,) = read.assignees
    assert dana.handle is None
    assert (dana.name, dana.username) == ("Dana Smith", "dana")


def test_two_assignees_are_both_on_the_issue():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW, assignees=[person(17), person(23, "Lee", "lee")])]

    (read,) = adapter(gitlab).harvest(Cursor()).work.issues

    assert [str(p.handle) for p in read.assignees] == [
        "gitlab:gitlab.example.com:17",
        "gitlab:gitlab.example.com:23",
    ]


def test_a_member_marked_bot_is_one_and_a_member_with_no_bot_key_is_not_stated():
    gitlab = GitLab()
    gitlab.members = [
        person(5, "Release Bot", "release-bot", bot=True, state="active", email="x@y"),
        person(17, "Dana Smith", "dana", state="active"),
        person(23, "Lee", "lee-bot", bot=False, state="blocked"),
    ]

    result = adapter(gitlab).harvest(Cursor())

    bot, dana, lee = result.work.members
    assert bot.person.bot is True
    assert dana.person.bot is None, "no `bot` key is not stated, never guessed"
    assert lee.person.bot is False, "a username is never read for it"
    assert (lee.state, dana.state) == ("blocked", "active")
    assert section(result, Section.MEMBERS).complete
    assert gitlab.asked("members")[0]["per_page"] == ["100"]


# ── Issues ───────────────────────────────────────────────────────────────────


def test_a_milestone_keeps_its_title_and_due_date_and_none_is_none():
    gitlab = GitLab()
    gitlab.issues = [
        issue(1, NOW, milestone={"title": "M1", "due_date": "2026-11-01", "id": 3}),
        issue(2, NOW, milestone={"title": "M2", "due_date": None}),
        issue(3, NOW),
    ]

    one, two, three = adapter(gitlab).harvest(Cursor()).work.issues

    assert (one.milestone_title, one.milestone_due) == ("M1", date(2026, 11, 1))
    assert (two.milestone_title, two.milestone_due) == ("M2", None)
    assert (three.milestone_title, three.milestone_due) == (None, None)


def test_an_issue_closed_since_the_cursor_is_recorded_closed_and_out_of_the_open_set():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW - timedelta(days=2))]
    first = adapter(gitlab).harvest(Cursor())
    gitlab.issues = [issue(1, NOW - timedelta(hours=1), state="closed", closed_at=stamp(NOW - timedelta(hours=1)))]

    (read,) = adapter(gitlab).harvest(first.cursor).work.issues

    assert read.state == "closed" and read.closed_at == NOW - timedelta(hours=1)
    assert not is_open(read.state)


def test_a_confidential_issue_keeps_its_iid_people_and_dates_and_not_its_title_or_labels():
    gitlab = GitLab()
    gitlab.issues = [
        issue(
            1, NOW, confidential=True, title="Payroll leak", labels=["security"],
            assignees=[person(17)], milestone={"title": "M1", "due_date": "2026-11-01"},
        )
    ]

    (read,) = adapter(gitlab).harvest(Cursor()).work.issues

    assert read.title == CONFIDENTIAL_ISSUE_TITLE and read.labels == ()
    assert read.confidential and read.assignees and read.milestone_title == "M1"


def test_an_issue_updated_mid_walk_is_read_again_and_none_is_skipped():
    """Paging by the filter: page 1 again from the last instant read."""
    gitlab = GitLab()
    gitlab.PER_PAGE = 2
    t = [NOW - timedelta(hours=10 - n) for n in range(10)]
    gitlab.issues = [issue(1, t[0]), issue(2, t[1]), issue(3, t[2]), issue(4, t[3])]
    moved = []

    def before(resource, query):
        # Issue 1, on the first page, is updated as soon as the walk moves on:
        # an offset walk would now skip issue 3, shifted onto the page it read.
        if resource == "issues" and query.get("updated_after") and not moved:
            moved.append(1)
            gitlab.issues[0] = issue(1, t[5], title="renamed")

    gitlab.before = before

    result = adapter(gitlab).harvest(Cursor())

    read = {i.iid: i for i in result.work.issues}
    assert sorted(read) == [1, 2, 3, 4]
    assert read[1].title == "renamed" and read[1].updated_at == t[5]
    queries = gitlab.asked("issues")
    assert [q["page"] for q in queries][:2] == [["1"], ["1"]]
    assert at(queries[1]["updated_after"][0]) == t[1].replace(microsecond=0)


def test_a_whole_page_already_read_at_one_instant_asks_the_next_page_of_that_filter():
    gitlab = GitLab()
    gitlab.PER_PAGE = 2
    same = NOW - timedelta(hours=1)
    gitlab.issues = [issue(1, same), issue(2, same), issue(3, same)]

    result = adapter(gitlab).harvest(Cursor())

    assert sorted(i.iid for i in result.work.issues) == [1, 2, 3]
    pages = [(q["page"][0], q.get("updated_after", [None])[0]) for q in gitlab.asked("issues")]
    assert pages[0] == ("1", None)
    assert pages[1][0] == "1" and pages[2][0] == "2", "the same filter's next page"
    assert section(result, Section.ISSUES).complete


def test_no_issue_or_merge_request_body_is_carried():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW)]
    gitlab.merge_requests = [merge_request(2, NOW)]

    result = adapter(gitlab).harvest(Cursor())

    assert "the body of" not in repr(result.work)


# ── Merge requests ───────────────────────────────────────────────────────────


def test_a_merge_request_first_seen_merged_is_recorded_merged():
    gitlab = GitLab()
    gitlab.merge_requests = [merge_request(2, NOW - timedelta(days=1))]
    first = adapter(gitlab).harvest(Cursor())
    gitlab.merge_requests = [
        merge_request(3, NOW - timedelta(hours=2), state="merged", merged_at=stamp(NOW - timedelta(hours=2)))
    ]

    (read,) = adapter(gitlab).harvest(first.cursor).work.merge_requests

    assert read.state == "merged" and read.merged_at == NOW - timedelta(hours=2)
    assert not is_open(read.state)


def test_a_locked_merge_request_is_kept_with_its_raw_state_in_the_open_set():
    """`state=opened` does not list a locked one, so it arrives on an incremental run."""
    gitlab = GitLab()
    first = adapter(gitlab).harvest(Cursor())
    gitlab.merge_requests = [merge_request(2, NOW, state="locked")]

    (read,) = adapter(gitlab).harvest(first.cursor).work.merge_requests

    assert read.state == "locked" and is_open(read.state)


def test_an_older_gitlabs_work_in_progress_is_draft_and_neither_is_false():
    gitlab = GitLab()
    older = merge_request(2, NOW, work_in_progress=True)
    del older["draft"]
    neither = merge_request(3, NOW)
    del neither["draft"]
    gitlab.merge_requests = [older, neither, merge_request(4, NOW, draft=True)]

    two, three, four = adapter(gitlab).harvest(Cursor()).work.merge_requests

    assert (two.draft, three.draft, four.draft) == (True, False, True)


def test_links_keep_this_projects_issues_only():
    gitlab = GitLab()
    gitlab.merge_requests = [merge_request(2, NOW)]
    gitlab.related[2] = [
        issue(12, NOW),
        {**issue(7, NOW), "project_id": 99},
        {key: value for key, value in issue(5, NOW).items() if key != "project_id"},
    ]

    (read,) = adapter(gitlab).harvest(Cursor()).work.merge_requests

    assert read.issue_iids == (12,)
    assert gitlab.sent[-1].endswith("/merge_requests/2/related_issues?per_page=100&page=1")


def test_the_link_cap_stops_the_merge_request_cursor_at_the_last_one_linked():
    gitlab = GitLab()
    base = NOW - timedelta(days=5)
    gitlab.merge_requests = [merge_request(i, base + timedelta(minutes=i)) for i in range(1, 601)]

    result = adapter(gitlab).harvest(Cursor())

    assert len(result.work.merge_requests) == 500
    assert len(gitlab.asked("related_issues")) == 500
    assert at(positions(result.cursor)["merge_requests"]) == base + timedelta(minutes=500)
    assert result.failure is None, "the cap is not a failure"
    assert not section(result, Section.MERGE_REQUESTS).complete

    gitlab.sent.clear()
    following = adapter(gitlab).harvest(result.cursor)
    assert {mr.iid for mr in following.work.merge_requests} >= set(range(501, 601))


def test_a_merge_request_for_a_confidential_issue_names_it_as_confidential():
    gitlab = GitLab()
    gitlab.merge_requests = [merge_request(2, NOW, title='Resolve "Payroll leak"')]
    gitlab.related[2] = [issue(1, NOW, confidential=True)]

    result = adapter(gitlab).harvest(Cursor())

    assert result.work.confidential_issue_iids == frozenset({1})
    assert result.work.merge_requests[0].issue_iids == (1,)


def test_a_403_on_one_merge_requests_links_leaves_them_unread():
    gitlab = GitLab()
    gitlab.merge_requests = [merge_request(2, NOW), merge_request(3, NOW + timedelta(seconds=1))]
    gitlab.related[3] = [issue(12, NOW)]
    gitlab.refuse["related_issues:2"] = refused(403)

    two, three = adapter(gitlab).harvest(Cursor()).work.merge_requests

    assert not two.links_read and three.links_read and three.issue_iids == (12,)


# ── The daily open check ─────────────────────────────────────────────────────


def test_a_stale_open_check_lists_the_open_set_completely_after_the_incremental_walk():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW - timedelta(days=3)), issue(2, NOW - timedelta(days=3))]
    first = adapter(gitlab, now=lambda: NOW - timedelta(days=2)).harvest(Cursor())
    del gitlab.issues[1]
    gitlab.sent.clear()

    result = adapter(gitlab).harvest(first.cursor)

    queries = gitlab.asked("issues")
    assert queries[0]["state"] == ["all"]
    assert queries[-1]["state"] == ["opened"] and "updated_after" not in queries[-1]
    assert result.work.open_issue_iids == frozenset({1})
    assert at(positions(result.cursor)["issues_checked"]) == NOW


def test_a_fresh_open_check_is_not_repeated():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW - timedelta(days=3))]
    first = adapter(gitlab, now=lambda: NOW - timedelta(hours=4)).harvest(Cursor())
    gitlab.sent.clear()

    result = adapter(gitlab).harvest(first.cursor)

    assert [q["state"] for q in gitlab.asked("issues")] == [["all"]]
    assert result.work.open_issue_iids is None


# ── Stops and unread sections ────────────────────────────────────────────────


def test_a_budget_ending_on_page_two_of_members_names_members_and_changes_no_member():
    gitlab = GitLab()
    gitlab.PER_PAGE = 1
    gitlab.members = [person(17), person(23)]
    clock = Clock()

    def before(resource, query):
        if resource == "members" and query["page"] == "1":
            clock.at = NOW + timedelta(minutes=11)

    gitlab.before = before

    result = adapter(gitlab, now=clock).harvest(Cursor())

    assert result.work.members is None, "a cut walk replaces no member"
    read = section(result, Section.MEMBERS)
    assert not read.complete and "budget" in read.reason
    assert result.failure is not None and result.failure.retryable
    assert "members" in result.failure.reason
    assert "Not read this run: issues, merge requests" in result.failure.reason
    assert positions(result.cursor)["issues"] is None, "a list not started keeps its cursor"
    assert result.coverage is not None, "the commit walk's coverage stands"
    assert result.outcome is HarvestOutcome.FAILED


def test_a_403_on_members_is_an_unread_section_and_not_a_failure():
    gitlab = GitLab()
    gitlab.refuse["members"] = refused(403)
    gitlab.issues = [issue(1, NOW)]

    result = adapter(gitlab).harvest(Cursor())

    read = section(result, Section.MEMBERS)
    assert not read.complete and "403" in read.reason
    assert result.work.members is None
    assert result.failure is None
    assert [i.iid for i in result.work.issues] == [1], "the rest is read"


def test_a_404_on_issues_reads_commits_and_merge_requests_and_claims_commit_coverage():
    gitlab = GitLab()
    gitlab.refuse["issues"] = refused(404)
    gitlab.commits = [commit("abc", at=NOW - timedelta(hours=1))]
    gitlab.merge_requests = [merge_request(2, NOW)]

    result = adapter(gitlab).harvest(Cursor())

    read = section(result, Section.ISSUES)
    assert not read.complete and "404" in read.reason
    assert result.failure is None
    assert len(result.events) == 1 and result.coverage is not None
    assert [mr.iid for mr in result.work.merge_requests] == [2]
    assert positions(result.cursor)["issues"] is None, "asked again next run"


def test_a_429_on_the_merge_request_list_applies_what_was_read_and_stops_with_the_hint():
    gitlab = GitLab()
    gitlab.PER_PAGE = 1
    gitlab.merge_requests = [merge_request(2, NOW - timedelta(hours=2)), merge_request(3, NOW - timedelta(hours=1))]
    asked = []

    def before(resource, query):
        if resource == "merge_requests":
            asked.append(query)
            if len(asked) == 2:
                gitlab.refuse["merge_requests"] = refused(429, **{"Retry-After": "120"})

    gitlab.before = before

    result = adapter(gitlab).harvest(Cursor())

    assert [mr.iid for mr in result.work.merge_requests] == [2]
    assert at(positions(result.cursor)["merge_requests"]) == NOW - timedelta(hours=2)
    assert result.failure.retryable and result.failure.retry_after == timedelta(seconds=120)
    assert "merge requests" in result.failure.reason
    assert not section(result, Section.MERGE_REQUESTS).complete
    assert section(result, Section.ISSUES).complete


def test_a_commit_walk_that_fails_reads_no_work_and_moves_no_cursor():
    gitlab = GitLab()
    gitlab.refuse["commits"] = refused(500)
    stored = Cursor(b'{"commits":null,"issues":null,"issues_checked":null,"merge_requests":null,"merge_requests_checked":null}')

    result = adapter(gitlab).harvest(stored)

    assert result.work is None and result.cursor == stored
    assert gitlab.asked("members") == []


def test_the_project_id_on_the_merge_request_decides_which_links_are_this_projects():
    gitlab = GitLab()
    gitlab.merge_requests = [merge_request(2, NOW, project_id=99)]
    gitlab.related[2] = [issue(12, NOW)]

    (read,) = adapter(gitlab).harvest(Cursor()).work.merge_requests

    assert read.issue_iids == (), f"issue 12 is project {PROJECT_ID}'s, not 99's"


# ── Review round: cursors, the open check, links ─────────────────────────────


def test_a_second_run_asks_for_commits_from_the_stored_commit_position_and_keeps_it():
    gitlab = GitLab()
    gitlab.commits = [commit("abc", at=NOW - timedelta(hours=2))]
    first = adapter(gitlab).harvest(Cursor())
    assert positions(first.cursor)["commits"] == "2026-10-18T07:00:00Z"
    gitlab.sent.clear()
    gitlab.commits = []

    second = adapter(gitlab, now=lambda: NOW + timedelta(hours=4)).harvest(first.cursor)

    assert gitlab.asked("commits")[0]["since"] == ["2026-09-18T07:00:00Z"], "30 days before"
    assert positions(second.cursor)["commits"] == "2026-10-18T07:00:00Z", "no newer commit"


def test_a_commit_only_run_keeps_the_work_positions_of_a_json_cursor():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW - timedelta(days=1))]
    first = adapter(gitlab).harvest(Cursor())
    commits_only = adapter(gitlab, reads_work=False)
    gitlab.commits = [commit("def", at=NOW - timedelta(minutes=30))]

    result = commits_only.harvest(first.cursor)

    before, after = positions(first.cursor), positions(result.cursor)
    assert after["commits"] == "2026-10-18T08:30:00Z"
    assert {k: v for k, v in after.items() if k != "commits"} == {
        k: v for k, v in before.items() if k != "commits"
    }


def test_a_foreign_json_cursor_is_refused_and_left_as_it_was():
    foreign = Cursor(b'{"calendar": null, "channels": null}')

    result = adapter(GitLab()).harvest(foreign)

    assert result.outcome is HarvestOutcome.FAILED and result.cursor == foreign
    assert not result.failure.retryable


def test_a_stale_open_check_cut_short_changes_nothing_but_the_incremental_rows_stand():
    gitlab = GitLab()
    gitlab.PER_PAGE = 1
    gitlab.issues = [issue(1, NOW - timedelta(days=3)), issue(2, NOW - timedelta(days=3, hours=1))]
    first = adapter(gitlab, now=lambda: NOW - timedelta(days=2)).harvest(Cursor())
    checked = positions(first.cursor)["issues_checked"]
    gitlab.issues.append(issue(3, NOW - timedelta(hours=1)))

    def before(resource, query):
        if resource == "issues" and query["state"] == "opened" and query["page"] == "2":
            gitlab.refuse["issues"] = refused(429, **{"Retry-After": "60"})

    gitlab.before = before

    result = adapter(gitlab).harvest(first.cursor)

    assert result.work.open_issue_iids is None, "nothing is judged gone"
    assert positions(result.cursor)["issues_checked"] == checked
    assert 3 in {i.iid for i in result.work.issues}, "the incremental walk ran first"
    assert at(positions(result.cursor)["issues"]) == NOW - timedelta(hours=1)
    assert "changed nothing" in result.failure.reason and result.failure.retryable
    assert "Not read this run: merge requests" in result.failure.reason


def test_an_open_check_dated_after_the_runs_start_is_checked():
    gitlab = GitLab()
    gitlab.issues = [issue(1, NOW - timedelta(days=3))]
    first = adapter(gitlab, now=lambda: NOW + timedelta(hours=5)).harvest(Cursor())
    gitlab.sent.clear()

    result = adapter(gitlab).harvest(first.cursor)

    assert [q["state"] for q in gitlab.asked("issues")] == [["all"], ["opened"]]
    assert at(positions(result.cursor)["issues_checked"]) == NOW


def test_a_merge_request_with_no_project_id_has_its_links_unread():
    gitlab = GitLab()
    row = merge_request(2, NOW)
    del row["project_id"]
    gitlab.merge_requests = [row]
    gitlab.related[2] = [issue(12, NOW)]

    (read,) = adapter(gitlab).harvest(Cursor()).work.merge_requests

    assert not read.links_read and read.issue_iids == ()
    assert gitlab.asked("related_issues") == [], "nothing to filter the links by"


def test_a_port_outside_the_range_gives_no_handle():
    from pm_ai.domain.handles import InvalidPersonHandle, gitlab_handle, person_handle

    assert gitlab_handle("https://gitlab.example.com:70000", 17) is None
    assert gitlab_handle("https://gitlab.example.com:0", 17) is None
    with pytest.raises(InvalidPersonHandle):
        person_handle("gitlab", "gitlab.example.com:99999:17")
