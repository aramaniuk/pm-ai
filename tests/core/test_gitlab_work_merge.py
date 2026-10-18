"""Story 8t — the merge of a GitLab harvest's change into the stored rows, and the one reader.

The merge is `pm_ai.domain.work.merge_work`, applied by the single writer's
`record_project_work` in one transaction; the reader is
`StoragePort.project_work(scope)`. Every row here runs against a real
operational store in a temporary root. The one-instance-per-project refusal
is `pm_ai.core.connector_enrolment.assert_unconfigured`'s.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from claim_fakes import InMemoryClaims

from pm_ai.core.connector_enrolment import assert_unconfigured
from pm_ai.domain.handles import gitlab_handle
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.work import (
    CLOSED,
    CONFIDENTIAL_ISSUE_TITLE,
    CONFIDENTIAL_MERGE_REQUEST_TITLE,
    GONE,
    MERGED,
    NEVER_READ,
    GitLabPerson,
    Issue,
    Member,
    MergeRequest,
    Section,
    SectionRead,
    WorkChange,
)
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import DuplicateConnector
from pm_ai.storage.crypto import AesGcmCrypto
from pm_ai.storage.service import StorageService

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
ALPHA = DataScope(ScopeKind.PROJECT, "alpha")
APPLICATION = DataScope(ScopeKind.APPLICATION)
HOST = "https://gitlab.example.com"
INSTANCE = "gitlab:alpha"


class _NoRepository:
    def working_tree(self, path):
        return None

    def repository_marker_above(self, path):
        return None

    def tracking(self, path, *, repository):  # pragma: no cover
        raise AssertionError("tracking asked with no working tree")


class Clock:
    def __init__(self, at: datetime = NOW) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def storage(tmp_path: Path, clock: Clock) -> StorageService:
    return StorageService(
        ScopePaths.rooted(tmp_path),
        now=clock,
        vcs=_NoRepository(),
        crypto=AesGcmCrypto(b"K" * 32),
        claims=InMemoryClaims(),
    )


def dana(username: str = "dana", user_id: int = 17) -> GitLabPerson:
    return GitLabPerson(gitlab_handle(HOST, user_id), "Dana Smith", username, None)


def an_issue(iid: int, updated: datetime = NOW, **fields) -> Issue:
    values = dict(
        iid=iid,
        title=f"issue {iid}",
        state="opened",
        assignees=(),
        milestone_title=None,
        milestone_due=None,
        labels=(),
        updated_at=updated,
        closed_at=None,
        web_url=None,
        confidential=False,
    )
    values.update(fields)
    return Issue(**values)


def a_merge_request(iid: int, updated: datetime = NOW, **fields) -> MergeRequest:
    values = dict(
        iid=iid,
        title=f"merge request {iid}",
        state="opened",
        source_branch=f"{iid}-branch",
        author=dana(),
        draft=False,
        updated_at=updated,
        merged_at=None,
        closed_at=None,
        web_url=None,
    )
    values.update(fields)
    return MergeRequest(**values)


ALL_READ = (
    SectionRead(Section.MEMBERS, complete=True),
    SectionRead(Section.ISSUES, complete=True),
    SectionRead(Section.MERGE_REQUESTS, complete=True),
)


def change(started: datetime = NOW, **fields) -> WorkChange:
    fields.setdefault("sections", ALL_READ)
    return WorkChange(instance=INSTANCE, started_at=started, **fields)


# ── The reader ───────────────────────────────────────────────────────────────


def test_a_scope_nothing_was_recorded_for_reads_as_never_read(storage):
    work = storage.project_work(ALPHA)

    assert work.instance is None and work.issues == () and work.members == ()
    assert all(not work.status(s).complete and work.status(s).reason == NEVER_READ for s in Section)


def test_what_is_recorded_reads_back_whole(storage):
    storage.record_project_work(
        ALPHA,
        change(
            members=(Member(dana(), "active"), Member(GitLabPerson(None, "Bot", "bot", True), None)),
            issues=(an_issue(1, assignees=(dana(), dana("lee", 23)), labels=("bug", "p1")),),
            merge_requests=(a_merge_request(2, issue_iids=(1,)),),
        ),
    )

    work = storage.project_work(ALPHA)

    assert work.instance == INSTANCE
    assert [m.person for m in work.members] == [dana(), GitLabPerson(None, "Bot", "bot", True)]
    (issue,) = work.issues
    assert [str(p.handle) for p in issue.assignees] == [
        "gitlab:gitlab.example.com:17",
        "gitlab:gitlab.example.com:23",
    ]
    assert issue.labels == ("bug", "p1")
    assert work.merge_requests[0].issue_iids == (1,)
    assert all(work.status(s).complete and work.status(s).last_complete == NOW for s in Section)


def test_no_stored_column_holds_a_description_a_comment_or_an_email(storage, tmp_path):
    storage.record_project_work(ALPHA, change(issues=(an_issue(1),)))

    db = sqlite3.connect(tmp_path / ".pm-ai" / "private" / "operational.db")
    columns = {
        column[1]
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'gitlab_%'")
        for column in db.execute(f"PRAGMA table_info({table})")
    }
    assert not {c for c in columns if "description" in c or "comment" in c or "email" in c}


def test_work_is_kept_per_project_scope_only(storage):
    with pytest.raises(ValueError, match="project scope"):
        storage.record_project_work(DataScope(ScopeKind.PERSONAL), change())


# ── People ───────────────────────────────────────────────────────────────────


def test_a_changed_username_is_replaced_and_the_handle_is_not(storage):
    storage.record_project_work(ALPHA, change(members=(Member(dana("dana"), "active"),)))
    storage.record_project_work(
        ALPHA, change(NOW + timedelta(hours=4), members=(Member(dana("dana.smith"), "active"),))
    )

    (member,) = storage.project_work(ALPHA).members
    assert member.person.username == "dana.smith"
    assert str(member.person.handle) == "gitlab:gitlab.example.com:17"


# ── State and prune ──────────────────────────────────────────────────────────


def test_a_closed_issue_leaves_the_open_set_and_is_kept_30_days(storage, clock):
    storage.record_project_work(ALPHA, change(issues=(an_issue(1),)))
    closed = NOW + timedelta(hours=1)
    storage.record_project_work(
        ALPHA,
        change(closed, issues=(an_issue(1, closed, state=CLOSED, closed_at=closed),)),
    )

    (issue,) = storage.project_work(ALPHA).issues
    assert issue.state == CLOSED and issue.closed_at == closed


def test_a_merge_request_first_seen_merged_is_shown_in_the_window(storage):
    merged = NOW - timedelta(days=3)
    storage.record_project_work(
        ALPHA, change(merge_requests=(a_merge_request(2, merged, state=MERGED, merged_at=merged),))
    )

    (mr,) = storage.project_work(ALPHA).merge_requests
    assert mr.state == MERGED and mr.merged_at == merged


@pytest.mark.parametrize(
    "closed_before, kept",
    [(timedelta(days=30), True), (timedelta(days=30, seconds=1), False)],
    ids=["exactly 30 days: kept", "one second earlier: dropped"],
)
def test_the_prune_boundary_is_thirty_days_before_the_runs_start(storage, clock, closed_before, kept):
    closed = NOW - closed_before
    storage.record_project_work(
        ALPHA, change(NOW - timedelta(days=40), issues=(an_issue(1, closed, state=CLOSED, closed_at=closed),))
    )

    storage.record_project_work(ALPHA, change(NOW))

    assert [i.iid for i in storage.project_work(ALPHA).issues] == ([1] if kept else [])


def test_a_drop_is_a_change(storage, clock):
    closed = NOW - timedelta(days=31)
    storage.record_project_work(
        ALPHA, change(NOW - timedelta(days=32), issues=(an_issue(1, closed, state=CLOSED, closed_at=closed),))
    )

    assert storage.record_project_work(ALPHA, change(NOW)) == 1


def test_a_closed_item_with_no_closing_time_is_pruned_by_its_update(storage):
    updated = NOW - timedelta(days=31)
    storage.record_project_work(
        ALPHA, change(NOW, issues=(an_issue(1, updated, state=CLOSED, closed_at=None),))
    )

    assert storage.project_work(ALPHA).issues == ()


def test_an_issue_missing_from_the_daily_open_check_is_recorded_gone(storage):
    storage.record_project_work(ALPHA, change(issues=(an_issue(1), an_issue(2))))
    later = NOW + timedelta(days=1, hours=1)

    storage.record_project_work(ALPHA, change(later, open_issue_iids=frozenset({1})))

    issues = {i.iid: i for i in storage.project_work(ALPHA).issues}
    assert issues[1].state == "opened"
    assert issues[2].state == GONE and issues[2].gone_at == later


def test_a_locked_merge_request_is_never_judged_gone_by_the_open_listing(storage):
    storage.record_project_work(ALPHA, change(merge_requests=(a_merge_request(2, state="locked"),)))

    storage.record_project_work(ALPHA, change(open_merge_request_iids=frozenset()))

    assert storage.project_work(ALPHA).merge_requests[0].state == "locked"


# ── Confidential ─────────────────────────────────────────────────────────────


def test_a_merge_request_linked_only_to_a_confidential_issue_keeps_no_title_or_branch(storage):
    secret = an_issue(1, confidential=True, title=CONFIDENTIAL_ISSUE_TITLE, assignees=(dana(),))
    storage.record_project_work(
        ALPHA,
        change(
            issues=(secret, an_issue(3)),
            merge_requests=(
                a_merge_request(2, title='Resolve "Payroll leak"', source_branch="1-payroll-leak", issue_iids=(1,)),
                a_merge_request(4, issue_iids=(1, 3)),
            ),
        ),
    )

    two, four = storage.project_work(ALPHA).merge_requests
    assert (two.title, two.source_branch) == (CONFIDENTIAL_MERGE_REQUEST_TITLE, None)
    assert four.title == "merge request 4", "linked to an open issue too"


def test_a_confidential_issue_learned_from_a_link_redacts_its_merge_request(storage):
    storage.record_project_work(
        ALPHA,
        change(
            merge_requests=(a_merge_request(2, title='Resolve "x"', issue_iids=(1,)),),
            confidential_issue_iids=frozenset({1}),
        ),
    )

    assert storage.project_work(ALPHA).merge_requests[0].title == CONFIDENTIAL_MERGE_REQUEST_TITLE


def test_an_issue_cannot_be_built_confidential_with_its_own_title():
    with pytest.raises(ValueError, match="confidential"):
        an_issue(1, confidential=True, title="Payroll leak")


# ── Links ────────────────────────────────────────────────────────────────────


def test_links_not_read_keep_the_links_stored_before(storage):
    storage.record_project_work(ALPHA, change(merge_requests=(a_merge_request(2, issue_iids=(5,)),)))

    storage.record_project_work(
        ALPHA,
        change(
            NOW + timedelta(hours=4),
            merge_requests=(a_merge_request(2, NOW + timedelta(hours=3), links_read=False),),
        ),
    )

    (mr,) = storage.project_work(ALPHA).merge_requests
    assert mr.issue_iids == (5,) and not mr.links_read


# ── Sections ─────────────────────────────────────────────────────────────────


def test_a_run_cut_short_keeps_the_last_complete_read(storage):
    eight = NOW.replace(hour=8)
    storage.record_project_work(ALPHA, change(eight))

    storage.record_project_work(
        ALPHA,
        change(
            NOW + timedelta(hours=4),
            sections=(SectionRead(Section.ISSUES, complete=False, reason="the budget ran out"),),
        ),
    )

    status = storage.project_work(ALPHA).status(Section.ISSUES)
    assert (status.complete, status.reason, status.last_complete) == (False, "the budget ran out", eight)
    assert storage.project_work(ALPHA).status(Section.MEMBERS).complete, "not mentioned: unchanged"


def test_hidden_members_keep_their_rows_and_their_last_complete_read(storage):
    storage.record_project_work(ALPHA, change(members=(Member(dana(), "active"),)))

    storage.record_project_work(
        ALPHA,
        change(
            NOW + timedelta(hours=4),
            members=None,
            sections=(SectionRead(Section.MEMBERS, complete=False, reason="GitLab answered 403"),),
        ),
    )

    work = storage.project_work(ALPHA)
    assert [m.person for m in work.members] == [dana()]
    assert work.status(Section.MEMBERS).last_complete == NOW
    assert work.status(Section.MEMBERS).reason == "GitLab answered 403"


# ── Re-runs ──────────────────────────────────────────────────────────────────


def test_a_change_recorded_twice_replaces_rows_with_themselves(storage):
    """A crash after the rows and before the cursor: the next run re-reads the same."""
    same = change(
        members=(Member(dana(), "active"),),
        issues=(an_issue(1, assignees=(dana(),)),),
        merge_requests=(a_merge_request(2, issue_iids=(1,)),),
    )
    assert storage.record_project_work(ALPHA, same) == 3
    first = storage.project_work(ALPHA)

    assert storage.record_project_work(ALPHA, same) == 0
    assert storage.project_work(ALPHA) == first


def test_nothing_updated_and_nothing_to_prune_writes_no_item_row(storage):
    storage.record_project_work(ALPHA, change(members=(Member(dana(), "active"),), issues=(an_issue(1),)))

    assert storage.record_project_work(
        ALPHA, change(NOW + timedelta(hours=4), members=(Member(dana(), "active"),))
    ) == 0


def test_two_instances_rows_are_never_merged_by_number(storage):
    storage.record_project_work(ALPHA, change(issues=(an_issue(1, title="ours"),)))
    storage.record_project_work(
        ALPHA,
        WorkChange(
            instance="gitlab:other",
            started_at=NOW - timedelta(hours=1),
            issues=(an_issue(1, title="theirs"),),
            sections=ALL_READ,
        ),
    )

    work = storage.project_work(ALPHA)
    assert work.instance == INSTANCE, "the one with the latest complete read"
    assert [i.title for i in work.issues] == ["ours"]


# ── One GitLab instance per project scope (AD-10) ────────────────────────────


def _row(storage: StorageService, instance: str, system: str = "gitlab") -> None:
    storage.write_artifact(
        json.dumps({"instance": instance, "system": system, "enabled": True}).encode(),
        scope=APPLICATION,
        artifact="connectors/",
        name=f"{instance}.json",
    )


def _into_alpha(instance: str) -> str | None:
    return "alpha"


def test_a_second_enrolled_gitlab_row_for_a_project_is_refused_naming_the_first(storage):
    _row(storage, "gitlab:alpha")

    with pytest.raises(DuplicateConnector, match="'gitlab:alpha'"):
        assert_unconfigured(storage, system="gitlab", instance="gitlab:web", files_into=_into_alpha)


def test_the_first_enrolment_replaces_the_built_in_and_is_accepted(storage):
    """Only the credential-less built-in `gitlab:alpha` exists: it is no row."""
    assert_unconfigured(storage, system="gitlab", instance="gitlab:alpha", files_into=_into_alpha)


def test_a_row_of_another_system_or_another_project_is_not_a_second_gitlab(storage):
    _row(storage, "graph:work", system="graph")
    _row(storage, "gitlab:beta")

    assert_unconfigured(
        storage,
        system="gitlab",
        instance="gitlab:alpha",
        files_into=lambda name: name.removeprefix("gitlab:"),
    )


# ── Review round ─────────────────────────────────────────────────────────────


def test_the_reader_drops_an_item_once_it_is_older_than_30_days_by_the_clock(storage, clock):
    closed = NOW - timedelta(days=29)
    storage.record_project_work(
        ALPHA, change(issues=(an_issue(1, closed, state=CLOSED, closed_at=closed),))
    )
    assert [i.iid for i in storage.project_work(ALPHA).issues] == [1]

    clock.at = NOW + timedelta(days=2)

    assert storage.project_work(ALPHA).issues == (), "no new record, and still out of the window"


def test_a_redacted_merge_request_re_read_with_links_unread_stays_redacted(storage):
    secret = an_issue(1, confidential=True, title=CONFIDENTIAL_ISSUE_TITLE)
    storage.record_project_work(
        ALPHA,
        change(
            issues=(secret,),
            merge_requests=(a_merge_request(2, title='Resolve "x"', source_branch="1-x", issue_iids=(1,)),),
        ),
    )
    # The issue is pruned from what is stored, so only the held row knows.
    later = NOW + timedelta(hours=1)
    storage.record_project_work(
        ALPHA,
        change(
            later,
            issues=(an_issue(1, later, state=CLOSED, closed_at=NOW - timedelta(days=40),
                             confidential=True, title=CONFIDENTIAL_ISSUE_TITLE),),
            merge_requests=(
                a_merge_request(2, later, title='Resolve "x"', source_branch="1-x", links_read=False),
            ),
        ),
    )

    (mr,) = storage.project_work(ALPHA).merge_requests
    assert (mr.title, mr.source_branch) == (CONFIDENTIAL_MERGE_REQUEST_TITLE, None)


def test_instants_are_stored_as_utc_whatever_offset_they_arrive_in(storage, tmp_path):
    from datetime import timezone as tz

    plus_two = tz(timedelta(hours=2))
    storage.record_project_work(
        ALPHA, change(NOW.astimezone(plus_two), issues=(an_issue(1, NOW.astimezone(plus_two)),))
    )

    db = sqlite3.connect(tmp_path / ".pm-ai" / "private" / "operational.db")
    (updated,) = db.execute("SELECT updated_at FROM gitlab_issues").fetchone()
    (last,) = db.execute("SELECT MAX(last_complete) FROM gitlab_work_status").fetchone()
    assert updated.endswith("+00:00") and last.endswith("+00:00")


def test_a_merge_request_missing_from_the_daily_check_is_recorded_gone_through_a_harvest(
    storage, clock
):
    from gitlab_fakes import TOKEN, GitLab, merge_request

    from pm_ai.connectors.gitlab import GitLabConnectorAdapter

    gitlab = GitLab()
    gitlab.merge_requests = [
        merge_request(2, NOW - timedelta(days=3)),
        merge_request(3, NOW - timedelta(days=3)),
    ]

    def harvest(cursor, at):
        clock.at = at
        result = GitLabConnectorAdapter(
            instance=INSTANCE, project="acme/web", scope=ALPHA, host=HOST, now=clock,
            credential=TOKEN, transport=gitlab, reads_work=True,
        ).harvest(cursor)
        storage.record_project_work(ALPHA, result.work)
        return result

    from pm_ai.domain.harvest import Cursor

    first = harvest(Cursor(), NOW - timedelta(days=2))
    del gitlab.merge_requests[1]
    harvest(first.cursor, NOW)

    mrs = {mr.iid: mr for mr in storage.project_work(ALPHA).merge_requests}
    assert mrs[2].state == "opened"
    assert mrs[3].state == GONE and mrs[3].gone_at == NOW
