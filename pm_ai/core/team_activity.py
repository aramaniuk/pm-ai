"""Team Activity, as values: who holds which open issue, and what moved on it (story 23g).

The project dashboard's third section lists each member with their open
assigned GitLab issues and marks an issue as worked on lately when commits
reference it or a linked merge request moved. This module is everything about
that section except its Markdown: the bare-reference finder, the grouping of
`8t`'s record and the project's `commit_pushed` lines into members and marked
issues, the caps, and `placed_issue_numbers` — so `7e`, which fills the slot
under each issue (`IssueNotes`), can count the updates it could not place.

**Pure.** Everything it reads is handed in: `8t`'s record as
`StoragePort.project_work(scope)` answered it, the project's event log, the
render's instant and its look-back. No GitLab request, no model, no clock, and
nothing from the personal or `people` scope (AD-25).

**Never a ranking.** Members are ordered by name and issues by milestone; a
mark never reorders anything, and nothing here totals marks per member, rates
them, or compares one member with another or with another day.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from types import MappingProxyType

from pm_ai.core.cadence import CYCLE, CYCLE_JITTER, LOOK_BACK_CAP, LookBack, look_back
from pm_ai.core.event_log import Unplaceable, ingested_at, posted_at
from pm_ai.domain.event_entries import EventEntry, MalformedEntry
from pm_ai.domain.events import CommitPayload, ObservedEventType
from pm_ai.domain.harvest import HarvestFailure
from pm_ai.domain.work import (
    MERGED,
    GitLabPerson,
    Issue,
    MergeRequest,
    ProjectWork,
    Section,
    is_open,
)
from pm_ai.domain.payload_fields import decode_payload

__all__ = [
    "ACTIVE",
    "FETCH_BUDGET",
    "MAX_ISSUES_PER_MEMBER",
    "MAX_MEMBERS",
    "MAX_NAMES_WITHOUT_ISSUES",
    "MAX_UNASSIGNED",
    "MERGE_REQUEST_LEAD",
    "MERGE_REQUEST_SKEW",
    "NO_ISSUE_NOTES",
    "STALE_AFTER",
    "TEXT_LIMIT",
    "Assignee",
    "CommitLine",
    "CommitMark",
    "CommitRead",
    "IssueNotes",
    "IssueRow",
    "MemberGroup",
    "MergeRequestMark",
    "TeamActivity",
    "Unlinked",
    "WorkAnswer",
    "bare_references",
    "build_team_activity",
    "placed_issue_numbers",
    "read_commits",
    "sort_name",
]


WorkAnswer = ProjectWork | HarvestFailure | None
"""What reading `8t`'s record can honestly answer.

A `ProjectWork` is a read that completed — with `instance` `None` when no
GitLab harvest recorded anything for the scope. `None` is no record handed in
at all, said the same way. A `HarvestFailure` is a read that could not
complete, its reason stated as Proactive Enablement states an unreadable log.
"""

# ── Caps and windows ─────────────────────────────────────────────────────────

MAX_MEMBERS = 15
"""Members with open assigned issues listed before a closing count."""

MAX_ISSUES_PER_MEMBER = 5
"""Issues listed under one member before a line counting the rest."""

MAX_NAMES_WITHOUT_ISSUES = 10
"""Names on the no-open-issue line before a count."""

MAX_UNASSIGNED = 5
"""Unassigned issues with a mark or a note listed before a count."""

ACTIVE = "active"
"""GitLab's member state for an active member. A member whose state is absent
is treated as active: nothing recorded says otherwise, so no state is shown."""

FETCH_BUDGET = timedelta(minutes=10)
"""The GitLab client's `FETCH_BUDGET` (`pm_ai.connectors.gitlab.client`).

Restated here because `core` may not import a connector; a test pins the two
equal, so a change to one fails until the other follows."""

MERGE_REQUEST_LEAD = CYCLE + CYCLE_JITTER + FETCH_BUDGET
"""How much earlier than the commit window the merge-request window opens: 265
minutes. Merge requests carry only GitLab's own times, so an update made just
before a render and harvested just after it is still marked on the next page."""

MERGE_REQUEST_SKEW = timedelta(minutes=5)
"""A merge-request time later than the render by more than this cannot be
right yet: it marks nothing and is counted. Within it, the time is read as at
the render — the mark shows the render's instant — since a clock seconds ahead
is not a reason to drop the mark."""

STALE_AFTER = CYCLE + CYCLE_JITTER
"""A section last read completely longer ago than this, at the render, is said
to be as last read: 255 minutes, one harvest cycle and its jitter."""

TEXT_LIMIT = 100
"""Titles, labels, milestone titles, names and commit first lines are cut to
this many characters, after folding and before escaping."""


# ── The bare reference ───────────────────────────────────────────────────────

_HASH_NUMBER = re.compile(r"#([0-9]+)")
_NOT_BEFORE = frozenset("/&!")


def bare_references(message: str) -> frozenset[int]:
    """The issue numbers `message` names as bare references: `#41`, never `x#41`.

    A reference is `#` followed by ASCII digits only, with no letter, digit,
    `/`, `&` or `!` immediately before the `#` and no letter or digit right
    after the number. A leading zero, and `#0`, are not references. A
    reference qualified with a project path (`group/proj#5`) is ignored,
    because it may name another project's issue. A three-digit colour (`#333`)
    cannot be told from issue 333 and is read as one — a stated limit.
    """
    found: set[int] = set()
    for match in _HASH_NUMBER.finditer(message):
        start, end = match.span()
        if start > 0:
            before = message[start - 1]
            if before.isalnum() or before in _NOT_BEFORE:
                continue
        if end < len(message) and message[end].isalnum():
            continue
        digits = match.group(1)
        if digits.startswith("0"):
            continue
        found.add(int(digits))
    return frozenset(found)


# ── The slot `7e` fills ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IssueNotes:
    """Already-rendered rows keyed by issue iid, and closing lines (`7e`'s slot).

    Each row is the text of one list item: Team Activity places it as a bullet
    under its issue, wherever that issue appears — under each assignee, or
    under Unassigned. The closing lines follow the unlinked-work line, one
    paragraph each. Nothing in either is escaped here: `7e` renders and
    escapes its own rows. The default, `NO_ISSUE_NOTES`, prints nothing.
    """

    rows: Mapping[int, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    closing: tuple[str, ...] = ()


NO_ISSUE_NOTES = IssueNotes()


# ── Commit lines off the event log ───────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommitLine:
    """One `commit_pushed` line recorded in the commit window.

    `recorded_at` is pm-ai's own recording time (`ingested_at`), which placed
    it in the window. `committed_at` is the commit time when it can be used —
    `None` when it is absent, `unknown`, flagged implausible or later than the
    render (`event_log.posted_at`).
    """

    entry_id: str
    sha: str
    message: str
    recorded_at: datetime
    committed_at: datetime | None

    @property
    def first_line(self) -> str:
        lines = self.message.splitlines()
        return lines[0] if lines else ""

    @property
    def references(self) -> frozenset[int]:
        return bare_references(self.message)


@dataclass(frozen=True, slots=True)
class CommitRead:
    """The commit lines in the window, and what the window could not use.

    `lines` holds the commits recorded in the window whose commit time is not
    older than the 7-day look-back cap (or unknown). The counts are footnotes:
    `backfilled` commits recorded in the window but committed more than 7 days
    earlier; `unplaceable` lines whose `ingested_at` is absent or unreadable;
    `ahead` lines whose `ingested_at` is later than the render; `undecodable`
    lines in the window whose fields the decoder refused.
    """

    lines: tuple[CommitLine, ...] = ()
    backfilled: int = 0
    unplaceable: int = 0
    ahead: int = 0
    undecodable: int = 0


def read_commits(
    entries: Sequence[EventEntry], *, window: LookBack, now: datetime
) -> CommitRead:
    """The `commit_pushed` lines `entries` recorded inside `window`, up to `now`."""
    lines: list[CommitLine] = []
    backfilled = unplaceable = ahead = undecodable = 0
    oldest = now - LOOK_BACK_CAP
    for entry in entries:
        if entry.category is not ObservedEventType.COMMIT_PUSHED:
            continue
        at = ingested_at(entry)
        if isinstance(at, Unplaceable):
            unplaceable += 1
            continue
        if at > now:
            ahead += 1
            continue
        if at < window.since:
            continue
        try:
            payload = decode_payload(entry)
        except MalformedEntry:
            payload = None
        if not isinstance(payload, CommitPayload):
            undecodable += 1
            continue
        committed = posted_at(entry, now=now)
        if committed is not None and committed < oldest:
            backfilled += 1
            continue
        lines.append(
            CommitLine(
                entry_id=entry.entry_id or "",
                sha=payload.sha,
                message=payload.message,
                recorded_at=at,
                committed_at=committed,
            )
        )
    return CommitRead(
        lines=tuple(lines),
        backfilled=backfilled,
        unplaceable=unplaceable,
        ahead=ahead,
        undecodable=undecodable,
    )


# ── Marks ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommitMark:
    """`N commits` referencing one issue, and the latest of them."""

    count: int
    latest: CommitLine


@dataclass(frozen=True, slots=True)
class MergeRequestMark:
    """A linked merge request that moved: updated while open, or merged."""

    iid: int
    merged: bool
    at: datetime
    draft: bool


@dataclass(frozen=True, slots=True)
class IssueRow:
    """One open issue as the section places it, with every mark that holds."""

    issue: Issue
    commits: CommitMark | None
    merge_requests: tuple[MergeRequestMark, ...]

    @property
    def marked(self) -> bool:
        return self.commits is not None or bool(self.merge_requests)


def _latest(lines: Sequence[CommitLine]) -> CommitLine:
    """Newest by recording time, then by usable commit time, then by id.

    The key, in order: the recording time; whether the commit time is usable
    (a usable one wins a tie); the commit time (the recording time standing in
    when it is not usable); the commit id (sha); the entry id — so the choice
    is total and a re-render picks the same commit.
    """
    return max(
        lines,
        key=lambda line: (
            line.recorded_at,
            line.committed_at is not None,
            line.committed_at or line.recorded_at,
            line.sha,
            line.entry_id,
        ),
    )


# ── People ───────────────────────────────────────────────────────────────────


def sort_name(text: str | None) -> str:
    """A display name as the member order compares it: NFKC, case-folded.

    An absent name sorts as the empty string, before every name."""
    return unicodedata.normalize("NFKC", text or "").casefold()


@dataclass(frozen=True, slots=True)
class Assignee:
    """A person the section lists, as `8t` recorded them.

    `member` is `True` for a project member, `False` for an assignee whose
    handle is not among the members, and `None` when that cannot be said: the
    person has no handle, or the member list was never read completely.
    `state` is the member's state, `None` when not recorded or not a member.
    """

    person: GitLabPerson
    state: str | None
    member: bool | None

    @property
    def active(self) -> bool:
        return self.state is None or self.state == ACTIVE

    @property
    def order(self) -> tuple[str, str, str, str]:
        p = self.person
        handle = str(p.handle) if p.handle is not None else ""
        return (sort_name(p.name), p.username or "", handle, p.name or "")


@dataclass(frozen=True, slots=True)
class MemberGroup:
    """One person with open assigned issues: those shown, and how many are not."""

    assignee: Assignee
    issues: tuple[IssueRow, ...]
    hidden: int


@dataclass(frozen=True, slots=True)
class Unlinked:
    """Work in the window linked to no open issue in `8t`'s record.

    `unreferenced` (commits with no bare reference) and `closed_only` (commits
    referencing only closed or unknown issues) are `None` when the event log
    could not be read. `merge_requests` (marked merge requests linking no open
    issue) is `None` when there is no record to read them from: none recorded,
    or a record that could not be read. A marked merge request whose links
    could not be read is in neither count (`TeamActivity.unknown_links`).
    """

    unreferenced: int | None
    closed_only: int | None
    merge_requests: int | None


@dataclass(frozen=True, slots=True)
class TeamActivity:
    """Everything the section says, grouped and capped, before any Markdown.

    `listed` is `False` when there is no member list to show: no record, a
    record that could not be read, or neither the members nor the issues
    section read completely in the last harvest (`SectionStatus.complete`
    false for both).
    """

    work: WorkAnswer
    commits: CommitRead | None
    log_failure: HarvestFailure | None
    window: LookBack
    merge_request_since: datetime
    listed: bool
    groups: tuple[MemberGroup, ...]
    hidden_groups: int
    without_issues: tuple[Assignee, ...] | None
    """`None` when the line is omitted (issues unread, or members never read)."""
    hidden_without_issues: int
    unassigned: tuple[IssueRow, ...]
    hidden_unassigned_marked: int
    hidden_unassigned_noted: int
    quiet_unassigned: int
    unlinked: Unlinked
    unusable_merge_request_times: int
    unknown_links: int = 0
    """Marked merge requests whose links could not be read (`links_read` false,
    no links stored): counted here, never as linked or unlinked."""

    @property
    def record(self) -> ProjectWork | None:
        """`8t`'s record when one was read and holds a harvest, else `None`."""
        if isinstance(self.work, ProjectWork) and self.work.instance is not None:
            return self.work
        return None


def _priority(issue: Issue) -> tuple[int, date, int]:
    """Milestone due date earliest first, then a milestone with no due date,
    then no milestone, then iid."""
    if issue.milestone_due is not None:
        return (0, issue.milestone_due, issue.iid)
    if issue.milestone_title is not None:
        return (1, date.min, issue.iid)
    return (2, date.min, issue.iid)


def _person_key(person: GitLabPerson) -> tuple[str, ...]:
    """Grouping key: the handle, which is the identity; a person with none is
    grouped only with identical records."""
    if person.handle is not None:
        return ("handle", str(person.handle))
    return ("unhandled", person.name or "", person.username or "")


def build_team_activity(
    work: WorkAnswer,
    entries: Sequence[EventEntry] | HarvestFailure,
    *,
    now: datetime,
    since: LookBack | None = None,
    issue_notes: IssueNotes = NO_ISSUE_NOTES,
) -> TeamActivity:
    """Group `8t`'s record and the commit lines into what the section shows."""
    window = since if since is not None else look_back(None, now)
    mr_since = window.since - MERGE_REQUEST_LEAD
    if isinstance(entries, HarvestFailure):
        commits: CommitRead | None = None
        log_failure: HarvestFailure | None = entries
    else:
        commits = read_commits(entries, window=window, now=now)
        log_failure = None

    record = work if isinstance(work, ProjectWork) and work.instance is not None else None
    issues = record.issues if record is not None else ()
    merge_requests = record.merge_requests if record is not None else ()
    open_iids = frozenset(issue.iid for issue in issues if is_open(issue.state))

    # Merge-request marks, and the times that cannot be used.
    latest_usable = now + MERGE_REQUEST_SKEW
    unusable = 0
    unknown_links = 0
    mr_marks: dict[int, list[MergeRequestMark]] = {}
    unlinked_mrs: int | None = 0 if record is not None else None
    for mr in merge_requests:
        marks = _merge_request_marks(mr, since=mr_since, latest=latest_usable, now=now)
        if marks is None:
            unusable += 1
            continue
        if not marks:
            continue
        if not mr.links_read and not mr.issue_iids:
            unknown_links += 1
            continue
        if unlinked_mrs is not None and not any(iid in open_iids for iid in mr.issue_iids):
            unlinked_mrs += 1
        for iid in mr.issue_iids:
            mr_marks.setdefault(iid, []).extend(marks)

    # Commit marks, and the commits linked to no open issue.
    by_issue: dict[int, list[CommitLine]] = {}
    unreferenced: int | None = None
    closed_only: int | None = None
    if commits is not None:
        unreferenced = closed_only = 0
        for line in commits.lines:
            refs = line.references
            for iid in refs:
                by_issue.setdefault(iid, []).append(line)
            if not refs:
                unreferenced += 1
            elif not refs & open_iids:
                closed_only += 1

    def row(issue: Issue) -> IssueRow:
        lines = by_issue.get(issue.iid, ())
        marks = sorted(mr_marks.get(issue.iid, ()), key=lambda m: (m.iid, m.merged))
        return IssueRow(
            issue=issue,
            commits=CommitMark(len(lines), _latest(lines)) if lines else None,
            merge_requests=tuple(marks),
        )

    unlinked = Unlinked(unreferenced, closed_only, unlinked_mrs)
    empty = TeamActivity(
        work=work,
        commits=commits,
        log_failure=log_failure,
        window=window,
        merge_request_since=mr_since,
        listed=False,
        groups=(),
        hidden_groups=0,
        without_issues=None,
        hidden_without_issues=0,
        unassigned=(),
        hidden_unassigned_marked=0,
        hidden_unassigned_noted=0,
        quiet_unassigned=0,
        unlinked=unlinked,
        unusable_merge_request_times=unusable,
        unknown_links=unknown_links,
    )
    if record is None:
        return empty
    members_status = record.status(Section.MEMBERS)
    issues_status = record.status(Section.ISSUES)
    if not members_status.complete and not issues_status.complete:
        return empty

    membership_known = members_status.last_complete is not None
    members = {
        str(m.person.handle): m for m in record.members if m.person.handle is not None
    }
    bots = {handle for handle, m in members.items() if m.person.bot is True}

    def is_bot(person: GitLabPerson) -> bool:
        return person.bot is True or (
            person.handle is not None and str(person.handle) in bots
        )

    def assignee(person: GitLabPerson) -> Assignee:
        if person.handle is None or not membership_known:
            return Assignee(person, state=None, member=None)
        held = members.get(str(person.handle))
        if held is None:
            return Assignee(person, state=None, member=False)
        return Assignee(held.person, state=held.state, member=True)

    groups: dict[tuple[str, ...], tuple[Assignee, list[IssueRow]]] = {}
    unassigned_rows: list[IssueRow] = []
    for issue in sorted((i for i in issues if is_open(i.state)), key=_priority):
        issue_row = row(issue)
        people = [p for p in issue.assignees if not is_bot(p)]
        if not people:
            unassigned_rows.append(issue_row)
            continue
        seen: set[tuple[str, ...]] = set()
        for person in people:
            key = _person_key(person)
            if key in seen:
                continue
            seen.add(key)
            if key not in groups:
                groups[key] = (assignee(person), [])
            groups[key][1].append(issue_row)

    ordered = sorted(groups.items(), key=lambda kv: (kv[1][0].order, kv[0]))
    member_groups = tuple(
        MemberGroup(
            assignee=who,
            issues=tuple(rows[:MAX_ISSUES_PER_MEMBER]),
            hidden=max(0, len(rows) - MAX_ISSUES_PER_MEMBER),
        )
        for _, (who, rows) in ordered[:MAX_MEMBERS]
    )

    without: tuple[Assignee, ...] | None = None
    hidden_without = 0
    if issues_status.complete and membership_known:
        idle = sorted(
            (
                Assignee(m.person, state=m.state, member=True)
                for m in record.members
                if m.person.bot is not True
                and _person_key(m.person) not in groups
                and (m.state is None or m.state == ACTIVE)
            ),
            key=lambda a: (a.order, _person_key(a.person)),
        )
        without = tuple(idle[:MAX_NAMES_WITHOUT_ISSUES])
        hidden_without = max(0, len(idle) - MAX_NAMES_WITHOUT_ISSUES)

    eligible = [r for r in unassigned_rows if r.marked or issue_notes.rows.get(r.issue.iid)]
    hidden_eligible = eligible[MAX_UNASSIGNED:]
    return TeamActivity(
        work=work,
        commits=commits,
        log_failure=log_failure,
        window=window,
        merge_request_since=mr_since,
        listed=True,
        groups=member_groups,
        hidden_groups=max(0, len(ordered) - MAX_MEMBERS),
        without_issues=without,
        hidden_without_issues=hidden_without,
        unassigned=tuple(eligible[:MAX_UNASSIGNED]),
        hidden_unassigned_marked=sum(1 for r in hidden_eligible if r.marked),
        hidden_unassigned_noted=sum(1 for r in hidden_eligible if not r.marked),
        quiet_unassigned=len(unassigned_rows) - len(eligible),
        unlinked=unlinked,
        unusable_merge_request_times=unusable,
        unknown_links=unknown_links,
    )


def _naive(at: datetime) -> bool:
    return at.tzinfo is None or at.utcoffset() is None


def _merge_request_marks(
    mr: MergeRequest, *, since: datetime, latest: datetime, now: datetime
) -> list[MergeRequestMark] | None:
    """The marks `mr` gives its linked issues, or `None` for an unusable time.

    Updated: open, `updated_at` in the window. Merged: `merged_at` in the
    window. A time later than `latest` (the render plus the skew allowance),
    one with no timezone (a hand-edited row), or a merged merge request with
    no `merged_at`, is unusable. The storage never hands over a merge request
    without `updated_at` (`8t` drops it at harvest), so absence is checked
    only for `merged_at`. A time after the render but within the allowance is
    shown as the render's instant (`MERGE_REQUEST_SKEW`).
    """
    marks: list[MergeRequestMark] = []
    if is_open(mr.state):
        if _naive(mr.updated_at) or mr.updated_at > latest:
            return None
        if mr.updated_at >= since:
            marks.append(
                MergeRequestMark(mr.iid, merged=False, at=min(mr.updated_at, now), draft=mr.draft)
            )
    if mr.state == MERGED and mr.merged_at is None:
        return None
    if mr.merged_at is not None:
        if _naive(mr.merged_at) or mr.merged_at > latest:
            return None
        if mr.merged_at >= since:
            marks.append(
                MergeRequestMark(mr.iid, merged=True, at=min(mr.merged_at, now), draft=mr.draft)
            )
    return marks


def placed_issue_numbers(
    work: WorkAnswer,
    entries: Sequence[EventEntry] | HarvestFailure,
    *,
    now: datetime,
    since: LookBack | None = None,
    issue_notes: IssueNotes = NO_ISSUE_NOTES,
) -> frozenset[int]:
    """The iids Team Activity places on the page for these inputs.

    The same inputs `render_project_dashboard` is given, grouped by the same
    function, so `7e` counts exactly the updates the section could not place.
    """
    activity = build_team_activity(
        work, entries, now=now, since=since, issue_notes=issue_notes
    )
    placed = {r.issue.iid for g in activity.groups for r in g.issues}
    placed.update(r.issue.iid for r in activity.unassigned)
    return frozenset(placed)
