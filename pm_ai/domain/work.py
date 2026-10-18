"""A GitLab project's members, issues and merge requests, as current state (story 8t).

A project member's tasks are the project's GitLab issues, and `23g` shows them
per member with what moved lately. This module holds the values a GitLab
harvest reads them into, the change one harvest hands to the single writer,
and the one I/O-free merge the writer applies — so the rule for what is kept,
what leaves the open set and what is dropped is written once, here, and the
writer only saves what it returns.

**Tier 2, not the event log** (AD-3). The event log's natural key is the
reference (AD-34), so an issue's later states would be dropped as duplicates of
its first; this is current state that each harvest replaces, kept per
`(project scope, connector instance)` in `operational.db` and read through
`StoragePort.project_work(scope)`.

**What is kept, and nothing else.** No description, no comment, no email. Every
text field here was typed by a person on GitLab — titles, labels, milestone
titles, branch names, display names, usernames — and sits outside AD-48's
payload declarations, so a reader that puts one in a prompt sanitizes each at
the model boundary (AD-12).

Imports only sibling `pm_ai.domain` modules (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum

from pm_ai.domain.handles import PersonHandle
from pm_ai.domain.identity import DataScope

__all__ = [
    "CLOSED",
    "CONFIDENTIAL_ISSUE_TITLE",
    "CONFIDENTIAL_MERGE_REQUEST_TITLE",
    "GONE",
    "KEPT_AFTER_CLOSING",
    "MERGED",
    "NEVER_READ",
    "OPENED",
    "GitLabPerson",
    "Issue",
    "Member",
    "MergeRequest",
    "ProjectWork",
    "Section",
    "SectionRead",
    "SectionStatus",
    "StoredWork",
    "WorkChange",
    "is_open",
    "merge_work",
    "visible_work",
]

OPENED = "opened"
CLOSED = "closed"
MERGED = "merged"
GONE = "gone"
"""pm-ai's own state, never GitLab's: an item stored open that a complete
listing of the open set no longer named — deleted, moved, or hidden from the
token. It leaves the open set."""

_LEFT_THE_OPEN_SET = frozenset({CLOSED, MERGED, GONE})

KEPT_AFTER_CLOSING = timedelta(days=30)
"""How long an item stays after it was closed, merged or found gone, measured
back from the run's start: closed exactly this long before it is kept, a second
earlier is dropped."""

CONFIDENTIAL_ISSUE_TITLE = "confidential issue"
CONFIDENTIAL_MERGE_REQUEST_TITLE = "merge request for a confidential issue"

NEVER_READ = "not read yet: no GitLab harvest has completed this section"


class Section(Enum):
    """The three parts of a project's work, each read and reported on its own."""

    MEMBERS = "members"
    ISSUES = "issues"
    MERGE_REQUESTS = "merge_requests"


def is_open(state: str) -> bool:
    """Whether an item in `state` is in the open set.

    Everything but closed, merged and gone: `opened`, and any state GitLab
    names that pm-ai has no reading for (`locked`), kept raw.
    """
    return state not in _LEFT_THE_OPEN_SET


# ── People ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GitLabPerson:
    """A person GitLab named on an issue or merge request, or as a member.

    `handle` is `gitlab:<host>:<id>`, or `None` when GitLab gave no usable
    numeric id — that person is shown by name and never matched by id (AD-34).
    `name` and `username` are for display and for `7e`'s name matching,
    replaced on every harvest, never identity. `bot` is GitLab's own `bot`
    field: `True` or `False` when the answer carries it, `None` — not stated —
    when it does not; never guessed from a username.
    """

    handle: PersonHandle | None
    name: str | None
    username: str | None
    bot: bool | None = None


@dataclass(frozen=True, slots=True)
class Member:
    """One project member, as `GET /projects/:id/members/all` names them."""

    person: GitLabPerson
    state: str | None


# ── Items ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Issue:
    """One issue's current state. A confidential issue's title is never its own."""

    iid: int
    title: str
    state: str
    assignees: tuple[GitLabPerson, ...]
    milestone_title: str | None
    milestone_due: date | None
    labels: tuple[str, ...]
    updated_at: datetime
    closed_at: datetime | None
    web_url: str | None
    confidential: bool
    gone_at: datetime | None = None
    """When pm-ai found it gone (its own instant, the run's start), else `None`."""

    def __post_init__(self) -> None:
        if self.confidential and (
            self.title != CONFIDENTIAL_ISSUE_TITLE or self.labels
        ):
            raise ValueError(
                f"issue {self.iid} is confidential, and a confidential issue is "
                f"kept with the title {CONFIDENTIAL_ISSUE_TITLE!r} and no labels."
            )

    @property
    def left_at(self) -> datetime:
        """The instant its prune is measured from: when it left the open set."""
        if self.state == GONE and self.gone_at is not None:
            return self.gone_at
        return self.closed_at or self.updated_at


@dataclass(frozen=True, slots=True)
class MergeRequest:
    """One merge request's current state, and the issues of this project it relates to."""

    iid: int
    title: str
    state: str
    source_branch: str | None
    author: GitLabPerson | None
    draft: bool
    updated_at: datetime
    merged_at: datetime | None
    closed_at: datetime | None
    web_url: str | None
    issue_iids: tuple[int, ...] = ()
    links_read: bool = True
    """Whether `issue_iids` was read this time. `False` after a 403 or 404 on
    its `related_issues`: the links stored before are kept and marked unread."""
    gone_at: datetime | None = None

    @property
    def left_at(self) -> datetime:
        """The instant its prune is measured from: when it left the open set."""
        if self.state == GONE and self.gone_at is not None:
            return self.gone_at
        return self.merged_at or self.closed_at or self.updated_at


# ── Section reads ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SectionRead:
    """What one run did with one section: read it completely, or not, and why."""

    section: Section
    complete: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.complete == (self.reason is not None):
            raise ValueError(
                f"{self.section.value}: a complete read carries no reason, and an "
                f"unread one always says why."
            )


@dataclass(frozen=True, slots=True)
class SectionStatus:
    """A section as stored: complete or unread, why, and its last complete read."""

    section: Section
    complete: bool
    reason: str | None
    last_complete: datetime | None


# ── The change one harvest hands over ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkChange:
    """What one GitLab harvest read of the project's work, for the writer to merge.

    `started_at` is the run's start, by the connector's clock: the instant a
    complete read is stamped with, and the one prunes are measured back from.

    `members` is the whole member list from a complete walk, or `None` when the
    walk was not complete — then no member row changes. `issues` and
    `merge_requests` are the items read this run, each replacing its stored
    row. `open_issue_iids` and `open_merge_request_iids` are a complete listing
    of the open set from the daily check, or `None` when no complete check ran;
    an item stored open and missing from one is recorded gone.
    `confidential_issue_iids` are the issues the run learned are confidential
    from a merge request's links. `sections` says, per section reached, whether
    this run read it completely.
    """

    instance: str
    started_at: datetime
    members: tuple[Member, ...] | None = None
    issues: tuple[Issue, ...] = ()
    merge_requests: tuple[MergeRequest, ...] = ()
    open_issue_iids: frozenset[int] | None = None
    open_merge_request_iids: frozenset[int] | None = None
    confidential_issue_iids: frozenset[int] = frozenset()
    sections: tuple[SectionRead, ...] = ()

    def __post_init__(self) -> None:
        named = [read.section for read in self.sections]
        if len(named) != len(set(named)):
            raise ValueError("a change says at most one thing about each section")


@dataclass(frozen=True, slots=True)
class StoredWork:
    """One `(scope, instance)`'s rows, as the writer reads and writes them."""

    members: tuple[Member, ...] = ()
    issues: tuple[Issue, ...] = ()
    merge_requests: tuple[MergeRequest, ...] = ()
    statuses: tuple[SectionStatus, ...] = ()


def _status_of(statuses: tuple[SectionStatus, ...], section: Section) -> SectionStatus | None:
    for status in statuses:
        if status.section is section:
            return status
    return None


def _redact_issue(issue: Issue) -> Issue:
    if not issue.confidential:
        return issue
    return replace(issue, title=CONFIDENTIAL_ISSUE_TITLE, labels=())


def _redact_merge_request(mr: MergeRequest, confidential: frozenset[int]) -> MergeRequest:
    """A merge request linked only to confidential issues keeps neither title nor branch.

    GitLab's defaults ("Resolve \\"…\\"", `142-title-slug`) quote the issue's
    title, so a merge request whose every linked issue is confidential is kept
    under a fixed title and with no source branch.
    """
    if mr.issue_iids and all(iid in confidential for iid in mr.issue_iids):
        return replace(mr, title=CONFIDENTIAL_MERGE_REQUEST_TITLE, source_branch=None)
    return mr


def merge_work(current: StoredWork, change: WorkChange) -> StoredWork:
    """The stored rows after `change`: I/O-free, so a re-run replaces rows with themselves.

    - **Members** are replaced whole only when the change carries a complete
      walk.
    - **Items read** replace their stored rows: an opened item is added or
      replaced, a closed or merged one is recorded with its state and time and
      leaves the open set — first seen that way or not. A merge request whose
      links were not read keeps the links stored before.
    - **Gone:** with a complete open listing, an item stored `opened`, not
      read this run and not listed is recorded `gone` at the run's start. An
      item in another raw state (`locked`) is never listed there, so it is
      not judged by it.
    - **Prune:** an item that left the open set more than `KEPT_AFTER_CLOSING`
      before the run's start is dropped.
    - **Confidential:** an issue known confidential is stored under the fixed
      title with no labels, and a merge request linked only to confidential
      issues under its fixed title with no branch.
    - **Sections:** a complete read is stamped with the run's start; an unread
      one keeps its last complete read. A section the change says nothing
      about is unchanged.
    """
    started = change.started_at
    floor = started - KEPT_AFTER_CLOSING

    issues = {issue.iid: issue for issue in current.issues}
    read_issues = {issue.iid for issue in change.issues}
    for issue in change.issues:
        issues[issue.iid] = _redact_issue(issue)
    if change.open_issue_iids is not None:
        for iid, issue in list(issues.items()):
            # Only `opened`: a listing of `state=opened` never names another
            # raw state (`locked`), so its absence there says nothing.
            if iid in read_issues or issue.state != OPENED:
                continue
            if iid not in change.open_issue_iids:
                issues[iid] = replace(issue, state=GONE, gone_at=started)
    kept_issues = {iid: issue for iid, issue in issues.items() if is_open(issue.state) or issue.left_at >= floor}

    merge_requests = {mr.iid: mr for mr in current.merge_requests}
    read_mrs = {mr.iid for mr in change.merge_requests}
    for mr in change.merge_requests:
        held = merge_requests.get(mr.iid)
        if not mr.links_read:
            mr = replace(mr, issue_iids=held.issue_iids if held is not None else ())
            if held is not None and held.title == CONFIDENTIAL_MERGE_REQUEST_TITLE:
                # Its links were not read this time, so nothing says the
                # confidential issue stopped being its only one: the
                # redaction stands.
                mr = replace(mr, title=held.title, source_branch=held.source_branch)
        merge_requests[mr.iid] = mr
    if change.open_merge_request_iids is not None:
        for iid, mr in list(merge_requests.items()):
            if iid in read_mrs or mr.state != OPENED:
                continue
            if iid not in change.open_merge_request_iids:
                merge_requests[iid] = replace(mr, state=GONE, gone_at=started)
    confidential = frozenset(
        {iid for iid, issue in kept_issues.items() if issue.confidential}
        | change.confidential_issue_iids
    )
    kept_mrs = {
        iid: _redact_merge_request(mr, confidential)
        for iid, mr in merge_requests.items()
        if is_open(mr.state) or mr.left_at >= floor
    }

    statuses = []
    reads = {read.section: read for read in change.sections}
    for section in Section:
        held_status = _status_of(current.statuses, section)
        read = reads.get(section)
        if read is None:
            if held_status is not None:
                statuses.append(held_status)
            continue
        statuses.append(
            SectionStatus(
                section=section,
                complete=read.complete,
                reason=read.reason,
                last_complete=(
                    started
                    if read.complete
                    else (held_status.last_complete if held_status is not None else None)
                ),
            )
        )

    return StoredWork(
        members=change.members if change.members is not None else current.members,
        issues=tuple(kept_issues[iid] for iid in sorted(kept_issues)),
        merge_requests=tuple(kept_mrs[iid] for iid in sorted(kept_mrs)),
        statuses=tuple(statuses),
    )


# ── What a reader gets ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProjectWork:
    """A project's GitLab work, as `StoragePort.project_work(scope)` answers (story 8t).

    The members; the issues open, and closed or gone within 30 days; the merge
    requests open, and merged, closed or gone within 30 days, each with the
    iids of this project's issues it relates to; and per section whether the
    last run read it completely, why not, and when it last did — so `23g` can
    say when what it shows is older than one harvest cycle. `instance` is the
    connector the rows came from, `None` when no GitLab harvest has recorded
    any for the scope.
    """

    scope: DataScope
    instance: str | None
    members: tuple[Member, ...] = ()
    issues: tuple[Issue, ...] = ()
    merge_requests: tuple[MergeRequest, ...] = ()
    sections: tuple[SectionStatus, ...] = field(
        default_factory=lambda: tuple(
            SectionStatus(section, complete=False, reason=NEVER_READ, last_complete=None)
            for section in Section
        )
    )

    def status(self, section: Section) -> SectionStatus:
        """One section's status; never-read when nothing was recorded for it."""
        found = _status_of(self.sections, section)
        if found is None:
            return SectionStatus(section, complete=False, reason=NEVER_READ, last_complete=None)
        return found


def visible_work(
    scope: DataScope, instance: str | None, stored: StoredWork, *, now: datetime
) -> ProjectWork:
    """What a reader is shown of `stored` at `now`: the open set and the last 30 days."""
    floor = now - KEPT_AFTER_CLOSING
    sections = tuple(
        _status_of(stored.statuses, section)
        or SectionStatus(section, complete=False, reason=NEVER_READ, last_complete=None)
        for section in Section
    )
    return ProjectWork(
        scope=scope,
        instance=instance,
        members=stored.members,
        issues=tuple(i for i in stored.issues if is_open(i.state) or i.left_at >= floor),
        merge_requests=tuple(
            m for m in stored.merge_requests if is_open(m.state) or m.left_at >= floor
        ),
        sections=sections,
    )
