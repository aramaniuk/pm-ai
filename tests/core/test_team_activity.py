"""Team Activity on the project dashboard — one test per row of `23g`'s matrix.

Spec: `_bmad-output/specs/spec-pm-ai/stories/23g-team-activity.md`.

Pure, like the other rendering suites: no `tmp_path`, no clock read, and the
display zone is a fixed `+02:00`, so the render at 05:00Z is the PM's 07:00.
The grouping is asserted through `pm_ai.core.team_activity` where a value says
it more plainly than Markdown, and through the rendered section otherwise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.connectors.gitlab.client import FETCH_BUDGET as CLIENT_FETCH_BUDGET
from pm_ai.core.cadence import CYCLE, CYCLE_JITTER, LookBack, LookBackKind
from pm_ai.core.rendering import (
    HEADINGS,
    PROJECT_HEADINGS,
    TEAM_ACTIVITY,
    PmHandles,
    render_dashboard,
    render_project_dashboard,
)
from pm_ai.core.goal_register import GoalRegister
from pm_ai.core.team_activity import (
    FETCH_BUDGET,
    MERGE_REQUEST_LEAD,
    NO_ISSUE_NOTES,
    STALE_AFTER,
    IssueNotes,
    bare_references,
    build_team_activity,
    placed_issue_numbers,
)
from pm_ai.domain.event_entries import EventEntry
from pm_ai.domain.events import CommitPayload, ObservedEventType
from pm_ai.domain.handles import gitlab_handle
from pm_ai.domain.harvest import HarvestFailure
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.payload_fields import encode_payload
from pm_ai.domain.work import (
    CLOSED,
    CONFIDENTIAL_ISSUE_TITLE,
    MERGED,
    NEVER_READ,
    GitLabPerson,
    Issue,
    Member,
    MergeRequest,
    ProjectWork,
    Section,
    SectionStatus,
)

ALPHA = DataScope(ScopeKind.PROJECT, project_id="alpha")
DISPLAY = timezone(timedelta(hours=2))
NOBODY = PmHandles(handles=frozenset(), unrecorded=(), unchecked=())
HOST = "https://gitlab.example.com"
NOW = datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)
"""07:00 in the display zone: the morning render."""
LAST_RENDER = NOW - timedelta(days=1)
SINCE = LookBack(LAST_RENDER, LookBackKind.SINCE_LAST_RENDER, LAST_RENDER)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def person(user_id: int, name: str | None, username: str | None, *, bot: bool | None = False) -> GitLabPerson:
    return GitLabPerson(gitlab_handle(HOST, user_id), name, username, bot)


DANA = person(1, "Dana Smith", "dana")
SAM = person(2, "Sam Lee", "sam")


def issue(iid: int, *assignees: GitLabPerson, **fields) -> Issue:
    values: dict = dict(
        iid=iid,
        title=f"Issue {iid}",
        state="opened",
        assignees=assignees,
        milestone_title=None,
        milestone_due=None,
        labels=(),
        updated_at=NOW - timedelta(days=2),
        closed_at=None,
        web_url=None,
        confidential=False,
    )
    values.update(fields)
    return Issue(**values)


def merge_request(iid: int, *issue_iids: int, **fields) -> MergeRequest:
    values: dict = dict(
        iid=iid,
        title=f"MR {iid}",
        state="opened",
        source_branch=None,
        author=None,
        draft=False,
        updated_at=NOW - timedelta(days=3),
        merged_at=None,
        closed_at=None,
        web_url=None,
        issue_iids=issue_iids,
    )
    values.update(fields)
    return MergeRequest(**values)


def statuses(
    *,
    complete: bool = True,
    read_at: datetime = NOW - timedelta(hours=1),
    **overrides: SectionStatus,
) -> tuple[SectionStatus, ...]:
    out = []
    for section in Section:
        out.append(
            overrides.get(section.value)
            or SectionStatus(section, complete, None if complete else "403 Forbidden", read_at)
        )
    return tuple(out)


def work(
    *,
    members: tuple[Member, ...] = (Member(DANA, "active"), Member(SAM, "active")),
    issues: tuple[Issue, ...] = (),
    merge_requests: tuple[MergeRequest, ...] = (),
    sections: tuple[SectionStatus, ...] | None = None,
) -> ProjectWork:
    return ProjectWork(
        scope=ALPHA,
        instance="gitlab:alpha",
        members=members,
        issues=issues,
        merge_requests=merge_requests,
        sections=statuses() if sections is None else sections,
    )


_counter = iter(range(1, 10_000))


def commit(
    message: str,
    *,
    recorded: datetime = NOW - timedelta(hours=2),
    committed: datetime | str | None = None,
    sha: str | None = None,
) -> EventEntry:
    n = next(_counter)
    occurred = recorded if committed is None else committed
    fields: list[tuple[str, str]] = [
        ("ingested_at", recorded.isoformat()),
        ("occurred_at", occurred if isinstance(occurred, str) else occurred.isoformat()),
        *encode_payload(CommitPayload(sha=f"{n:08x}" + "a" * 32 if sha is None else sha, message=message)),
    ]
    return EventEntry(
        category=ObservedEventType.COMMIT_PUSHED,
        actor="email:dev@example.com",
        fields=tuple(fields),
        entry_id=f"evt_{n:05d}",
    )


def section(
    record: object = None,
    entries: object = (),
    *,
    now: datetime = NOW,
    since: LookBack | None = SINCE,
    notes: IssueNotes | None = None,
) -> str:
    text = render(record, entries, now=now, since=since, notes=notes)
    return text.split(f"## {TEAM_ACTIVITY}\n\n", 1)[1]


def render(
    record: object = None,
    entries: object = (),
    *,
    now: datetime = NOW,
    since: LookBack | None = SINCE,
    notes: IssueNotes | None = None,
) -> str:
    if notes is None:
        return render_project_dashboard(
            (), entries, now, tz=DISPLAY, pm=NOBODY, since=since, work=record  # type: ignore[arg-type]
        )
    return render_project_dashboard(
        (), entries, now, tz=DISPLAY, pm=NOBODY, since=since, work=record, issue_notes=notes  # type: ignore[arg-type]
    )


def block_of(text: str, head: str) -> str:
    """The lines of one member (or Unassigned) group, from its head to the next."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(f"- **{head}**"))
    out = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        out.append(line)
    return "\n".join(out)


def issue_block(text: str, iid: int, *, within: str | None = None) -> str:
    """One issue's line and the mark lines under it."""
    scope = text if within is None else within
    lines = scope.split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(f"  - `#{iid}`"))
    out = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.startswith("    "):
            break
        out.append(line)
    return "\n".join(out)


def heads(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.startswith("- **")]


# ── The constants the windows are built from ─────────────────────────────────


def test_the_fetch_budget_restated_in_core_is_the_clients():
    assert FETCH_BUDGET == CLIENT_FETCH_BUDGET


def test_the_merge_request_window_opens_265_minutes_earlier():
    assert MERGE_REQUEST_LEAD == CYCLE + CYCLE_JITTER + CLIENT_FETCH_BUDGET
    assert MERGE_REQUEST_LEAD == timedelta(minutes=265)
    assert STALE_AFTER == timedelta(minutes=255)


# ── The bare-reference finder ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "found"),
    [
        ("Fix #41", {41}),
        ("#41 at the start", {41}),
        ("(#41)", {41}),
        ("refs #41, #52.", {41, 52}),
        ("#41 and #41 again", {41}),
        ("closes:#7", {7}),
        ("#333", {333}),
        ("#9999999", {9999999}),
        ("&#123;", set()),
        ("abc#5", set()),
        ("!42", set()),
        ("#5a", set()),
        ("#041", set()),
        ("#0", set()),
        ("other/proj#5", set()),
        ("group/#5", set()),
        ("#٣", set()),
        ("#1٣", set()),
        ("é#5", set()),
        ("#5é", set()),
        ("1#5", set()),
        ("##5", {5}),
        ("no reference", set()),
    ],
)
def test_the_finder_matches_only_bare_references(message, found):
    """Matrix row "Not references", and the table of matches beside it."""
    assert bare_references(message) == frozenset(found)


def test_none_of_the_listed_non_references_is_counted():
    """Matrix row "Not references": `&#123;`, `abc#5`, `!42`, `#5a`, `#041`, `#0`,
    `other/proj#5`, `#٣` — none counted against an open issue 5, 41, 42, 123."""
    record = work(issues=(issue(5, DANA), issue(41, DANA), issue(42, DANA), issue(123, DANA)))
    messages = ("&#123;", "abc#5", "!42", "#5a", "#041", "#0", "other/proj#5", "#٣")
    activity = build_team_activity(
        record, tuple(commit(m) for m in messages), now=NOW, since=SINCE
    )
    assert all(row.commits is None for group in activity.groups for row in group.issues)
    assert activity.unlinked.unreferenced == len(messages)


# ── Marks ────────────────────────────────────────────────────────────────────


def test_an_ordinary_morning():
    """Matrix row "Ordinary morning"."""
    record = work(
        issues=(issue(41, DANA), issue(52, SAM)),
        merge_requests=(
            merge_request(9, 52, updated_at=NOW - timedelta(hours=10)),
        ),
    )
    entries = (
        commit("Start #41", recorded=NOW - timedelta(hours=5)),
        commit("Continue #41", recorded=NOW - timedelta(hours=4)),
        commit("Finish #41\n\nLong body", recorded=NOW - timedelta(hours=3), sha="deadbeef" + "0" * 32),
    )
    text = section(record, entries)
    dana = issue_block(text, 41)
    assert "- 3 commits; latest `deadbeef` at 04:00: Finish #41" in dana
    assert "Long body" not in dana
    sam = issue_block(text, 52)
    assert "- merge request `!9` updated 2026-10-17 21:00" in sam


def test_a_merged_merge_request_marks_its_issue():
    """Matrix row "MR merged"."""
    record = work(
        issues=(issue(41, DANA),),
        merge_requests=(
            merge_request(
                9, 41, state=MERGED, updated_at=NOW - timedelta(hours=1),
                merged_at=NOW - timedelta(hours=1),
            ),
        ),
    )
    block = issue_block(section(record), 41)
    assert "- merge request `!9` merged 06:00" in block
    assert "updated" not in block


def test_all_three_marks_are_shown():
    """Matrix row "All three"."""
    record = work(
        issues=(issue(41, DANA),),
        merge_requests=(
            merge_request(9, 41, updated_at=NOW - timedelta(hours=2), draft=True),
            merge_request(
                10, 41, state=MERGED, updated_at=NOW - timedelta(hours=1),
                merged_at=NOW - timedelta(hours=1),
            ),
        ),
    )
    block = issue_block(section(record, (commit("Fix #41"),)), 41)
    assert "- 1 commit; latest" in block
    assert "- merge request `!9` updated 05:00 (draft)" in block
    assert "- merge request `!10` merged 06:00" in block


def test_a_merge_request_updated_just_before_a_render_is_marked_on_the_next_page():
    """Matrix row "MR updated just before the render": updated 06:50, render
    07:00, harvested 08:00 — the next morning's page still marks it."""
    updated = NOW - timedelta(minutes=10)
    next_morning = NOW + timedelta(days=1)
    since = LookBack(NOW, LookBackKind.SINCE_LAST_RENDER, NOW)
    record = work(issues=(issue(41, DANA),), merge_requests=(merge_request(9, 41, updated_at=updated),))
    block = issue_block(section(record, now=next_morning, since=since), 41)
    assert "merge request `!9` updated 2026-10-18 06:50" in block


def test_a_merge_request_updated_only_before_the_window_misses_its_mark():
    """Matrix row "Missed harvest" — the stated limit: an update older than the
    merge-request window's opening marks nothing."""
    too_early = SINCE.since - MERGE_REQUEST_LEAD - timedelta(minutes=1)
    record = work(issues=(issue(41, DANA),), merge_requests=(merge_request(9, 41, updated_at=too_early),))
    activity = build_team_activity(record, (), now=NOW, since=SINCE)
    assert activity.groups[0].issues[0].merge_requests == ()
    just_inside = SINCE.since - MERGE_REQUEST_LEAD
    record = work(issues=(issue(41, DANA),), merge_requests=(merge_request(9, 41, updated_at=just_inside),))
    activity = build_team_activity(record, (), now=NOW, since=SINCE)
    assert activity.groups[0].issues[0].merge_requests


def test_an_unusable_merge_request_time_marks_nothing_and_is_counted():
    """Matrix row "Unusable MR time": a merged merge request with no `merged_at`,
    and an `updated_at` an hour after the render."""
    record = work(
        issues=(issue(41, DANA),),
        merge_requests=(
            merge_request(9, 41, state=MERGED, updated_at=NOW - timedelta(hours=1), merged_at=None),
            merge_request(10, 41, updated_at=NOW + timedelta(hours=1)),
            merge_request(11, 41, updated_at=NOW + timedelta(minutes=4)),
        ),
    )
    text = section(record)
    block = issue_block(text, 41)
    assert "`!9`" not in block and "`!10`" not in block
    assert "`!11` updated 07:00" in block  # within the allowance: shown at the render
    assert "2 merge requests carry a time that cannot be used" in text


def test_back_filled_commits_mark_nothing_and_are_counted():
    """Matrix row "Back-filled commits"."""
    record = work(issues=(issue(41, DANA),))
    entries = (
        commit("Old #41", committed=NOW - timedelta(weeks=3)),
        commit("Older #41", committed=NOW - timedelta(weeks=4)),
    )
    text = section(record, entries)
    assert "commit;" not in text and "commits;" not in text
    assert "2 commits recorded in the window were committed more than 7 days earlier" in text


def test_the_latest_commit_with_an_unknown_time_is_labelled_with_its_harvest_time():
    """Matrix row "Latest commit, unusable time"."""
    record = work(issues=(issue(41, DANA),))
    entries = (commit("Fix #41", recorded=NOW - timedelta(minutes=30), committed="unknown"),)
    assert "at 06:30 (harvested): Fix #41" in section(record, entries)


def test_a_commit_time_after_the_render_takes_the_harvest_label():
    record = work(issues=(issue(41, DANA),))
    entries = (commit("Fix #41", recorded=NOW - timedelta(minutes=30), committed=NOW + timedelta(hours=2)),)
    assert "at 06:30 (harvested)" in section(record, entries)


def test_the_latest_commit_is_newest_by_recording_time():
    record = work(issues=(issue(41, DANA),))
    entries = (
        commit("Recorded later #41", recorded=NOW - timedelta(hours=1), committed=NOW - timedelta(hours=9), sha="bbbbbbbb" + "0" * 32),
        commit("Committed later #41", recorded=NOW - timedelta(hours=2), committed=NOW - timedelta(hours=2), sha="aaaaaaaa" + "0" * 32),
    )
    assert "latest `bbbbbbbb` at 2026-10-17 22:00: Recorded later #41" in section(record, entries)


def test_a_commit_recorded_before_the_window_is_not_counted():
    record = work(issues=(issue(41, DANA),))
    entries = (commit("Fix #41", recorded=LAST_RENDER - timedelta(minutes=1)),)
    activity = build_team_activity(record, entries, now=NOW, since=SINCE)
    assert activity.groups[0].issues[0].commits is None


# ── Order ────────────────────────────────────────────────────────────────────


def test_issues_follow_milestone_priority_and_marks_do_not_reorder():
    """Matrix row "Priority order"."""
    record = work(
        issues=(
            issue(1, DANA),  # no milestone
            issue(2, DANA, milestone_title="Someday"),  # milestone without date
            issue(3, DANA, milestone_title="Late", milestone_due=NOW.date() + timedelta(weeks=3)),
            issue(4, DANA, milestone_title="Soon", milestone_due=NOW.date() + timedelta(weeks=1)),
            issue(5, DANA, milestone_title="Soon", milestone_due=NOW.date() + timedelta(weeks=1)),
        ),
    )
    entries = (commit("Fix #1"), commit("Fix #1 again"))
    activity = build_team_activity(record, entries, now=NOW, since=SINCE)
    assert [row.issue.iid for row in activity.groups[0].issues] == [4, 5, 3, 2, 1]
    text = section(record, entries)
    assert "`#4` Issue 4 — milestone Soon, due 2026-10-25" in text


def test_members_are_ordered_by_folded_name_then_username_then_handle():
    """Matrix row "Member order"."""
    zoe = person(10, "zoë", "zoe")
    zoe_upper = person(11, "ZOË", None)
    adam = person(12, "Adam", "adam")
    emile = person(13, "Émile", "emile")
    twin_b = person(14, "Kim", "kim_b")
    twin_a = person(15, "Kim", "kim_a")
    members = tuple(Member(p, "active") for p in (zoe, zoe_upper, adam, emile, twin_b, twin_a))
    record = work(
        members=members,
        issues=tuple(issue(20 + n, p) for n, p in enumerate((zoe, zoe_upper, adam, emile, twin_b, twin_a))),
    )
    assert heads(section(record)) == [
        "- **Adam** `@adam`",
        "- **Kim** `@kim_a`",
        "- **Kim** `@kim_b`",
        "- **ZOË**",
        "- **zoë** `@zoe`",
        "- **Émile** `@emile`",
    ]


# ── Who is listed ────────────────────────────────────────────────────────────


def test_members_with_no_open_issue_are_named_on_one_line():
    """Matrix row "No open issue"."""
    record = work(
        members=(
            Member(DANA, "active"),
            Member(person(3, "Ann", "ann"), "active"),
            Member(person(4, "Bo", "bo"), "active"),
            Member(person(5, "Cy", "cy"), None),
        ),
        issues=(issue(41, DANA),),
    )
    text = section(record)
    assert "Project members with no open assigned issue: Ann, Bo, Cy." in text
    assert heads(text) == ["- **Dana Smith** `@dana`"]


def test_forty_members_without_issues_name_ten_and_count_thirty():
    """Matrix row "Many without issues"."""
    many = tuple(Member(person(100 + n, f"Member {n:02d}", f"m{n:02d}"), "active") for n in range(40))
    text = section(work(members=many))
    line = next(line for line in text.split("\n") if line.startswith("Project members with no open"))
    assert line.endswith("Member 09, and 30 more.")
    assert line.count("Member ") == 10


def test_unread_issues_state_the_reason_and_drop_the_no_issue_line():
    """Matrix row "Issues unread"."""
    sections = statuses(
        issues=SectionStatus(Section.ISSUES, False, "403 Forbidden on /issues", NOW - timedelta(hours=1)),
    )
    text = section(work(issues=(issue(41, DANA),), sections=sections))
    assert "The issues could not be read completely in the last harvest: 403 Forbidden on /issues" in text
    assert "no open assigned issue" not in text
    assert "- **Dana Smith**" in text


def test_members_and_issues_both_unread_is_one_statement_and_no_list():
    """Matrix row "Members and issues unread"."""
    sections = statuses(
        members=SectionStatus(Section.MEMBERS, False, "401 Unauthorized", None),
        issues=SectionStatus(Section.ISSUES, False, "timed out", NOW - timedelta(hours=1)),
    )
    text = section(work(issues=(issue(41, DANA),), sections=sections))
    statement = [line for line in text.split("\n") if "401 Unauthorized" in line]
    assert len(statement) == 1 and "timed out" in statement[0]
    assert heads(text) == []
    assert "no open assigned issue" not in text


def test_a_bot_member_is_not_listed_or_counted():
    """Matrix row "Bot member"."""
    bot = person(9, "project_42_bot", "project_42_bot", bot=True)
    record = work(members=(Member(DANA, "active"), Member(bot, "active")), issues=(issue(41, DANA),))
    text = section(record)
    assert "bot" not in text
    assert "no open assigned issue" not in text


def test_a_bot_assignee_leaves_the_issue_unassigned():
    bot = person(9, "project_42_bot", "project_42_bot", bot=True)
    record = work(members=(Member(DANA, "active"), Member(bot, "active")), issues=(issue(41, bot),))
    activity = build_team_activity(record, (commit("Fix #41"),), now=NOW, since=SINCE)
    assert activity.groups == ()
    assert [row.issue.iid for row in activity.unassigned] == [41]


def test_a_member_whose_bot_flag_is_not_stated_is_listed():
    """Matrix row "Bot not stated"."""
    unknown = person(9, "ci", "ci", bot=None)
    record = work(members=(Member(DANA, "active"), Member(unknown, "active")), issues=(issue(41, DANA),))
    assert "Project members with no open assigned issue: ci." in section(record)


def test_a_section_read_nine_hours_ago_is_said_to_be_stale():
    """Matrix row "Stale section"."""
    sections = statuses(
        issues=SectionStatus(Section.ISSUES, True, None, NOW - timedelta(hours=9)),
    )
    text = section(work(issues=(issue(41, DANA),), sections=sections))
    stale = [line for line in text.split("\n") if "last read completely" in line]
    assert stale == ["The issues are shown as last read completely at 2026-10-17 22:00."]


def test_fresh_sections_print_no_staleness_line():
    """Matrix row "Fresh sections"."""
    sections = statuses(read_at=NOW - STALE_AFTER)
    text = section(work(issues=(issue(41, DANA),), sections=sections))
    assert "last read" not in text
    assert "never been read" not in text


def test_a_section_never_read_completely_is_said_so():
    sections = statuses(
        merge_requests=SectionStatus(Section.MERGE_REQUESTS, False, NEVER_READ, None),
    )
    text = section(work(issues=(issue(41, DANA),), sections=sections))
    assert f"The merge requests have never been read completely: {NEVER_READ}" in text


def test_an_inactive_member_is_listed_with_the_state_while_issues_are_assigned():
    """Matrix row "Inactive member"."""
    blocked = person(3, "Bea", "bea")
    gone = person(4, "Gus", "gus")
    record = work(
        members=(Member(blocked, "blocked"), Member(gone, "blocked"), Member(DANA, "active")),
        issues=(issue(41, blocked), issue(42, blocked)),
    )
    text = section(record)
    assert "- **Bea** `@bea` (blocked)" in text
    assert "`#41`" in block_of(text, "Bea") and "`#42`" in block_of(text, "Bea")
    assert "Gus" not in text
    assert "Project members with no open assigned issue: Dana Smith." in text


def test_an_assignee_who_is_no_longer_a_member_is_marked():
    """Matrix row "Former member"."""
    former = person(77, "Old Timer", "old")
    text = section(work(issues=(issue(41, former),)))
    assert "- **Old Timer** `@old` (not a project member)" in text


def test_a_member_list_never_read_marks_nobody_as_former():
    sections = statuses(
        members=SectionStatus(Section.MEMBERS, False, NEVER_READ, None),
    )
    text = section(work(members=(), issues=(issue(41, DANA),), sections=sections))
    assert "(not a project member)" not in text
    assert "- **Dana Smith**" in text


def test_the_caps_on_members_and_issues():
    """Matrix row "Over the caps": 18 members with issues; one with 9."""
    people = [person(200 + n, f"Person {n:02d}", f"p{n:02d}") for n in range(18)]
    issues = [issue(1000 + n, p) for n, p in enumerate(people)]
    issues += [issue(2000 + n, people[0]) for n in range(8)]
    record = work(members=tuple(Member(p, "active") for p in people), issues=tuple(issues))
    text = section(record)
    assert len(heads(text)) == 15
    assert "3 more members with open issues are not shown; their issues are in GitLab." in text
    first = block_of(text, "Person 00")
    assert first.count("  - `#") == 5
    assert "  - 4 more open issues assigned here are not shown; they are in GitLab." in first


def test_unassigned_issues_show_the_marked_and_count_the_rest():
    """Matrix row "Unassigned": 9 quiet and 6 marked."""
    issues = tuple(issue(n) for n in range(1, 16))
    entries = tuple(commit(f"Work on #{n}") for n in range(1, 7))
    text = section(work(issues=issues), entries)
    block = block_of(text, "Unassigned")
    assert block.count("  - `#") == 5
    assert [line.split("`")[1] for line in block.split("\n") if line.startswith("  - `#")] == [
        "#1", "#2", "#3", "#4", "#5",
    ]
    assert "  - 1 more marked unassigned issue is not shown; it is in GitLab." in block
    assert "  - 9 more open unassigned issues have no mark and are not shown." in block
    assert heads(text)[-1] == "- **Unassigned**"


# ── Unlinked work ────────────────────────────────────────────────────────────


def test_unlinked_work_is_counted_against_the_open_set():
    """Matrix row "Unlinked"."""
    record = work(issues=(issue(41, DANA), issue(12, DANA, state=CLOSED)))
    entries = (
        commit("tidy"),
        commit("tidy more"),
        commit("lint"),
        commit("format"),
        commit("Revert #12"),
        commit("Fix #12 and #41"),
    )
    text = section(record, entries)
    assert (
        "Unlinked work in the window: 4 commits reference no issue, 1 commit "
        "references only closed or unknown issues, and 0 marked merge requests "
        "link no open issue." in text
    )
    assert "- 1 commit; latest" in issue_block(text, 41)


def test_a_commit_to_an_issue_hidden_by_the_cap_is_linked():
    """Matrix row "Hidden by a cap"."""
    record = work(issues=tuple(issue(n, DANA) for n in range(1, 8)))
    activity = build_team_activity(record, (commit("Fix #7"),), now=NOW, since=SINCE)
    assert 7 not in {row.issue.iid for row in activity.groups[0].issues}
    assert activity.unlinked.unreferenced == 0 and activity.unlinked.closed_only == 0


def test_a_marked_merge_request_linking_no_open_issue_is_counted():
    record = work(
        issues=(issue(41, DANA), issue(12, state=CLOSED)),
        merge_requests=(
            merge_request(9, 12, updated_at=NOW - timedelta(hours=1)),
            merge_request(10, updated_at=NOW - timedelta(hours=1)),
            merge_request(11, 41, updated_at=NOW - timedelta(hours=1)),
        ),
    )
    assert "2 marked merge requests link no open issue" in section(record)


def test_a_colour_like_reference_is_read_as_an_issue():
    """Matrix row "Colour-like" — the stated limit."""
    entries = (commit("Set the border to #333"),)
    closed = build_team_activity(work(issues=(issue(41, DANA),)), entries, now=NOW, since=SINCE)
    assert closed.unlinked.closed_only == 1
    opened = build_team_activity(work(issues=(issue(333, DANA),)), entries, now=NOW, since=SINCE)
    assert opened.unlinked.closed_only == 0
    assert opened.groups[0].issues[0].commits is not None


# ── Texts ────────────────────────────────────────────────────────────────────


def test_a_confidential_issue_is_shown_as_such_with_its_marks():
    """Matrix row "Confidential"."""
    secret = issue(41, DANA, title=CONFIDENTIAL_ISSUE_TITLE, confidential=True)
    block = issue_block(section(work(issues=(secret,)), (commit("Fix #41"),)), 41)
    assert block.startswith("  - `#41` confidential issue")
    assert "- 1 commit; latest" in block


def test_a_long_title_with_a_break_is_folded_cut_and_escaped():
    """Matrix row "Long title with a break"."""
    title = "<b>" + "x" * 90 + "\n" + "y" * 106
    block = issue_block(section(work(issues=(issue(41, DANA, title=title),))), 41)
    shown = block.split("`#41` ", 1)[1]
    assert "\n" not in shown
    assert shown.startswith("\\<b\\>" + "x" * 90 + " ")
    assert shown.endswith("…")
    # 100 characters before escaping: the two escaped brackets add two.
    assert len(shown) == 100 + 2


def test_labels_milestones_names_and_commit_lines_are_escaped():
    dana = person(1, "Dana | `Smith`", "dana")
    record = work(
        members=(Member(dana, "active"),),
        issues=(issue(41, dana, milestone_title="M[1]", labels=("a|b", "c\nd")),),
    )
    text = section(record, (commit("Fix #41 <script>"),))
    assert "- **Dana \\| \\`Smith\\`**" in text
    assert "milestone M\\[1\\]" in text
    assert "labels: a\\|b, c d" in text
    assert ": Fix #41 \\<script\\>" in text


# ── The slot ─────────────────────────────────────────────────────────────────


RECORD = work(
    issues=(issue(41, DANA, SAM), issue(52, DANA), issue(12, state=CLOSED)),
    merge_requests=(merge_request(9, 52, updated_at=NOW - timedelta(hours=1)),),
)
ENTRIES = (commit("Fix #41"),)


def test_the_default_slot_prints_nothing_and_is_byte_identical():
    """Matrix row "Slot empty", and the acceptance criterion."""
    without = render(RECORD, ENTRIES)
    assert render(RECORD, ENTRIES, notes=NO_ISSUE_NOTES) == without
    assert render(RECORD, ENTRIES, notes=IssueNotes()) == without


def test_rows_are_placed_under_the_issue_for_every_assignee():
    """Matrix row "Slot filled"."""
    notes = IssueNotes(rows={41: ("Dana: done by Friday — *from the standup*",)})
    text = section(RECORD, ENTRIES, notes=notes)
    for head in ("Dana Smith", "Sam Lee"):
        block = issue_block(text, 41, within=block_of(text, head))
        assert block.endswith("    - Dana: done by Friday — *from the standup*")


def test_rows_for_an_unlisted_issue_are_not_placed_and_closing_lines_follow_unlinked():
    """Matrix row "Slot for an unlisted issue"."""
    notes = IssueNotes(rows={12: ("A note on a closed issue",)}, closing=("1 update could not be placed.",))
    text = section(RECORD, ENTRIES, notes=notes)
    assert "A note on a closed issue" not in text
    assert 12 not in placed_issue_numbers(RECORD, ENTRIES, now=NOW, since=SINCE, issue_notes=notes)
    paragraphs = text.rstrip("\n").split("\n\n")
    unlinked = next(i for i, p in enumerate(paragraphs) if p.startswith("Unlinked work"))
    assert paragraphs[unlinked + 1] == "1 update could not be placed."


def test_placed_issue_numbers_follows_the_caps_and_the_notes():
    issues = tuple(issue(n, DANA) for n in range(1, 8)) + (issue(20), issue(21))
    record = work(issues=issues)
    assert placed_issue_numbers(record, (), now=NOW, since=SINCE) == frozenset({1, 2, 3, 4, 5})
    notes = IssueNotes(rows={21: ("noted",)})
    assert placed_issue_numbers(record, (), now=NOW, since=SINCE, issue_notes=notes) == frozenset(
        {1, 2, 3, 4, 5, 21}
    )
    assert "    - noted" in section(record, notes=notes)


# ── Nothing invented ─────────────────────────────────────────────────────────


def test_no_record_says_so_and_still_prints_the_unlinked_line():
    """Matrix row "No record"."""
    for record in (None, ProjectWork(scope=ALPHA, instance=None)):
        text = section(record, (commit("Fix #41"), commit("tidy")))
        assert "No GitLab issues are recorded for this project" in text
        assert (
            "Unlinked work in the window: 1 commit references no issue, 1 commit "
            "references only closed or unknown issues" in text
        )
        assert heads(text) == []


def test_an_unreadable_record_is_stated_and_the_other_sections_render():
    """Matrix row "Record unreadable" — the malformed record."""
    failure = HarvestFailure(reason="operational.db: database disk image is malformed", retryable=False)
    text = render(failure, (commit("Fix #41"),))
    assert list(PROJECT_HEADINGS) == [line[3:] for line in text.split("\n") if line.startswith("## ")]
    body = text.split(f"## {TEAM_ACTIVITY}\n\n", 1)[1]
    assert "The project's GitLab record could not be read" in body
    assert "database disk image is malformed" in body


def test_an_unreadable_log_is_stated_and_the_record_still_renders():
    """Matrix row "Record unreadable" — the corrupt log."""
    failure = HarvestFailure(reason="2026-10.md line 3: corrupt", retryable=False)
    record = work(
        issues=(issue(52, DANA),),
        merge_requests=(merge_request(9, 52, updated_at=NOW - timedelta(hours=1)),),
    )
    text = section(record, failure)
    assert "The event log could not be read, so no commit marks an issue: 2026-10.md line 3: corrupt" in text
    assert "merge request `!9` updated" in text
    assert "Unlinked work in the window: commits unknown" in text


def test_log_lines_that_cannot_be_used_are_counted():
    record = work(issues=(issue(41, DANA),))
    no_stamp = EventEntry(
        category=ObservedEventType.COMMIT_PUSHED,
        actor="email:dev@example.com",
        fields=encode_payload(CommitPayload(sha="a" * 40, message="Fix #41")),
        entry_id="evt_nostamp",
    )
    no_message = EventEntry(
        category=ObservedEventType.COMMIT_PUSHED,
        actor="email:dev@example.com",
        fields=(("ingested_at", (NOW - timedelta(hours=1)).isoformat()), ("sha", "b" * 40)),
        entry_id="evt_nomessage",
    )
    ahead = commit("Fix #41", recorded=NOW + timedelta(hours=1))
    text = section(record, (no_stamp, no_message, ahead))
    assert "1 commit log entry carries no readable `ingested_at`" in text
    assert "1 commit log entry carries an `ingested_at` later than this render's instant" in text
    assert "1 commit log entry carries fields that could not be decoded" in text


# ── Never a ranking ──────────────────────────────────────────────────────────


def test_no_member_level_total_or_comparison_is_printed():
    """Acceptance — "no member-level total appears".

    Checked where a total would sit: on a member's head line, which carries
    the name, the username, a state and the not-a-member marker and nothing
    else — no number, no count, no ordinal. Counts on mark lines are per
    issue, and footnotes are about the log, so neither is scanned here.
    """
    record = work(
        issues=(issue(41, DANA), issue(42, DANA), issue(52, SAM)),
        merge_requests=(
            merge_request(9, 42, updated_at=NOW - timedelta(hours=1)),
            merge_request(10, 42, state=MERGED, merged_at=None),  # a footnote
        ),
    )
    entries = tuple(commit(f"Fix #{n}") for n in (41, 41, 52)) + (
        commit("Old #41", committed=NOW - timedelta(weeks=3)),  # a footnote
    )
    text = section(record, entries)
    assert "cannot be used" in text and "more than 7 days earlier" in text
    member_heads = [h for h in heads(text) if h != "- **Unassigned**"]
    assert member_heads == ["- **Dana Smith** `@dana`", "- **Sam Lee** `@sam`"]
    for head in member_heads:
        assert not any(char.isdigit() for char in head)
        assert ";" not in head and ":" not in head
    # Only issue lines and mark lines sit under a member: no summary line.
    for name in ("Dana Smith", "Sam Lee"):
        for line in block_of(text, name).split("\n")[1:]:
            assert line.startswith("  - `#") or line.startswith("    - ")


def test_a_mark_never_reorders_members():
    record = work(issues=(issue(41, DANA), issue(52, SAM)))
    quiet = heads(section(record))
    busy = heads(section(record, tuple(commit("Fix #52") for _ in range(5))))
    assert quiet == busy == ["- **Dana Smith** `@dana`", "- **Sam Lee** `@sam`"]


def test_the_personal_dashboard_is_unchanged():
    """Matrix row "Personal dashboard": four sections, no Team Activity."""
    text = render_dashboard((), (), GoalRegister(present=False), NOW, tz=DISPLAY, pm=NOBODY)
    assert [line[3:] for line in text.split("\n") if line.startswith("## ")] == list(HEADINGS)
    assert TEAM_ACTIVITY not in text


def test_the_same_inputs_render_byte_identically_twice():
    """Matrix row "Same inputs twice"."""
    notes = IssueNotes(rows={41: ("row",)}, closing=("closing",))
    assert render(RECORD, ENTRIES, notes=notes) == render(RECORD, ENTRIES, notes=notes)


def test_team_activity_is_the_third_project_section():
    text = render(RECORD, ENTRIES)
    assert [line[3:] for line in text.split("\n") if line.startswith("## ")] == list(PROJECT_HEADINGS)
    assert PROJECT_HEADINGS[2] == TEAM_ACTIVITY


def test_the_first_line_names_both_windows():
    first = section(RECORD).split("\n", 1)[0]
    assert first == (
        "Open GitLab issues by assignee, in milestone order. Commits are counted "
        "since the last render at 2026-10-17 07:00; merge requests from "
        "2026-10-17 02:35 to 2026-10-18 07:00."
    )



def test_a_complete_record_with_no_open_issue_says_so():
    text = section(work(issues=(issue(12, DANA, state=CLOSED),)))
    assert "No open issue is recorded for this project." in text
    assert "Project members with no open assigned issue: Dana Smith, Sam Lee." in text


def test_issues_never_read_are_not_reported_as_none_open():
    sections = statuses(issues=SectionStatus(Section.ISSUES, False, NEVER_READ, None))
    text = section(work(sections=sections))
    assert "No open issue is recorded" not in text
    assert f"The issues have never been read completely: {NEVER_READ}" in text



# ── Review fixes ─────────────────────────────────────────────────────────────


def test_people_without_a_name_are_shown_by_username_handle_or_as_unnamed_and_sort_first():
    """(a) and (e): a name absent, both absent, and no handle at all."""
    no_name = person(30, None, "quiet")
    nothing = person(31, None, None)
    no_handle = GitLabPerson(None, None, None, False)
    record = work(
        members=(Member(DANA, "active"), Member(no_name, "active"), Member(nothing, "active")),
        issues=(issue(41, DANA), issue(42, no_name), issue(43, nothing), issue(44, no_handle)),
    )
    found = heads(section(record))
    assert found == [
        "- **an unnamed person**",
        f"- **`{nothing.handle}`**",
        "- **`@quiet`**",
        "- **Dana Smith** `@dana`",
    ]


def test_an_assignee_with_no_handle_is_never_marked_as_a_former_member():
    """(e)"""
    stranger = GitLabPerson(None, "Kept By Name", "kbn", None)
    text = section(work(issues=(issue(41, stranger),)))
    assert "- **Kept By Name** `@kbn`" in text
    assert "(not a project member)" not in text


def test_a_merged_merge_request_dated_an_hour_ahead_marks_nothing_and_is_counted():
    """(b)"""
    record = work(
        issues=(issue(41, DANA),),
        merge_requests=(merge_request(9, 41, state=MERGED, merged_at=NOW + timedelta(hours=1)),),
    )
    text = section(record)
    assert "`!9`" not in text
    assert "1 merge request carries a time that cannot be used" in text


def test_more_than_five_unassigned_issues_with_notes_count_the_rest():
    """(c)"""
    record = work(issues=tuple(issue(n) for n in range(1, 9)))
    notes = IssueNotes(rows={n: (f"note {n}",) for n in range(1, 9)})
    block = block_of(section(record, notes=notes), "Unassigned")
    assert block.count("  - `#") == 5
    assert "  - 3 more unassigned issues with notes are not shown; they are in GitLab." in block


def test_a_due_date_without_a_milestone_title():
    """(d): sorted with the dated milestones, shown as a due date."""
    record = work(
        issues=(
            issue(1, DANA, milestone_title="Named", milestone_due=NOW.date() + timedelta(days=9)),
            issue(2, DANA, milestone_due=NOW.date() + timedelta(days=2)),
        ),
    )
    text = section(record)
    assert "  - `#2` Issue 2 — due 2026-10-20" in text
    assert text.index("`#2`") < text.index("`#1`")


def test_an_inactive_assignee_who_is_not_a_member():
    """(f): a former member has no member state to show; the marker says why."""
    former = person(70, "Gone Away", "gone")
    blocked_former = Member(person(71, "Also Gone", "also"), "blocked")
    record = work(
        members=(Member(DANA, "active"),),
        issues=(issue(41, former), issue(42, blocked_former.person)),
    )
    text = section(record)
    assert "- **Gone Away** `@gone` (not a project member)" in text
    assert "- **Also Gone** `@also` (not a project member)" in text
    # And a blocked member still listed with the state:
    record = work(members=(Member(former, "blocked"),), issues=(issue(41, former),))
    assert "- **Gone Away** `@gone` (blocked)" in section(record)


def test_an_issue_assigned_to_a_bot_and_a_person_is_the_persons():
    """(g)"""
    bot = person(9, "project_bot", "project_bot", bot=True)
    record = work(members=(Member(DANA, "active"), Member(bot, "active")), issues=(issue(41, bot, DANA),))
    text = section(record)
    assert heads(text) == ["- **Dana Smith** `@dana`"]
    assert "`#41`" in block_of(text, "Dana Smith")
    assert "project_bot" not in text


def test_an_update_exactly_at_the_skew_limit_marks_with_the_time_clamped():
    """(h)"""
    from pm_ai.core.team_activity import MERGE_REQUEST_SKEW

    record = work(
        issues=(issue(41, DANA),),
        merge_requests=(merge_request(9, 41, updated_at=NOW + MERGE_REQUEST_SKEW),),
    )
    activity = build_team_activity(record, (), now=NOW, since=SINCE)
    (mark,) = activity.groups[0].issues[0].merge_requests
    assert mark.at == NOW
    assert "`!9` updated 07:00" in section(record)


def test_naive_merge_request_times_are_unusable_not_a_crash():
    """4: a hand-edited row with no timezone."""
    naive = (NOW - timedelta(hours=1)).replace(tzinfo=None)
    record = work(
        issues=(issue(41, DANA),),
        merge_requests=(
            merge_request(9, 41, updated_at=naive),
            merge_request(10, 41, state=MERGED, updated_at=NOW - timedelta(hours=1), merged_at=naive),
        ),
    )
    text = section(record)
    assert "`!9`" not in text and "`!10`" not in text
    assert "2 merge requests carry a time that cannot be used" in text


def test_a_naive_last_complete_read_is_said_as_never_read_not_a_crash():
    """4: chosen reading — never read completely."""
    naive = (NOW - timedelta(hours=1)).replace(tzinfo=None)
    sections = statuses(issues=SectionStatus(Section.ISSUES, True, None, naive))
    text = section(work(issues=(issue(41, DANA),), sections=sections))
    assert "The issues have never been read completely: no reason was recorded." in text


def test_an_ingested_at_with_no_utc_instant_is_unplaceable():
    """5"""
    from pm_ai.core.event_log import Unplaceable, ingested_at

    extreme = EventEntry(
        category=ObservedEventType.COMMIT_PUSHED,
        actor="email:dev@example.com",
        fields=(("ingested_at", "0001-01-01T00:00:00+05:00"),)
        + encode_payload(CommitPayload(sha="a" * 40, message="Fix #41")),
        entry_id="evt_extreme",
    )
    assert ingested_at(extreme) is Unplaceable.UNREADABLE
    text = section(work(issues=(issue(41, DANA),)), (extreme,))
    assert "1 commit log entry carries no readable `ingested_at`" in text


def test_a_merge_request_whose_links_were_not_read_is_not_counted_unlinked():
    """6"""
    record = work(
        issues=(issue(41, DANA),),
        merge_requests=(
            merge_request(9, updated_at=NOW - timedelta(hours=1), links_read=False),
            merge_request(10, updated_at=NOW - timedelta(hours=1)),
        ),
    )
    text = section(record)
    assert "and 1 marked merge request links no open issue." in text
    assert "1 marked merge request whose links could not be read is counted neither" in text


def test_the_unlinked_line_without_a_record_says_what_is_known_of_merge_requests():
    """7"""
    unread = section(HarvestFailure(reason="malformed", retryable=False), (commit("tidy"),))
    assert "merge requests unknown, the GitLab record could not be read." in unread
    assert "0 marked merge requests" not in unread
    none = section(None, (commit("tidy"),))
    assert "and no merge requests are recorded." in none
    assert "0 marked merge requests" not in none


def test_the_unlinked_line_says_when_issues_are_as_last_read():
    """7"""
    sections = statuses(
        issues=SectionStatus(Section.ISSUES, False, "timed out", NOW - timedelta(hours=1)),
    )
    text = section(work(issues=(issue(41, DANA),), sections=sections), (commit("tidy"),))
    line = next(line for line in text.split("\n") if line.startswith("Unlinked work"))
    assert line.endswith("link no open issue — checked against the issues as last read.")


def test_an_empty_sha_is_said_in_words():
    """8"""
    entry = commit("Fix #41", sha="")
    text = section(work(issues=(issue(41, DANA),)), (entry,))
    assert "latest (no commit id recorded) at" in text
    assert "``" not in text


@pytest.mark.parametrize("reason", ["403 Forbidden", "403 Forbidden.", "timed out..."])
def test_every_printed_sentence_ends_in_exactly_one_full_stop(reason):
    """10"""
    unread_both = statuses(
        members=SectionStatus(Section.MEMBERS, False, reason, None),
        issues=SectionStatus(Section.ISSUES, False, reason, NOW - timedelta(hours=9)),
        merge_requests=SectionStatus(Section.MERGE_REQUESTS, False, reason, NOW - timedelta(hours=9)),
    )
    one_unread = statuses(
        issues=SectionStatus(Section.ISSUES, False, reason, NOW - timedelta(hours=9)),
    )
    texts = [
        section(work(issues=(issue(41, DANA),), sections=unread_both)),
        section(work(issues=(issue(41, DANA),), sections=one_unread)),
        section(HarvestFailure(reason=reason, retryable=False), HarvestFailure(reason=reason, retryable=False)),
    ]
    for text in texts:
        for paragraph in text.rstrip("\n").split("\n\n"):
            if paragraph.startswith("- "):
                continue
            assert paragraph.endswith(".") and not paragraph.endswith(".."), paragraph
            assert ".;" not in paragraph and "..;" not in paragraph, paragraph
