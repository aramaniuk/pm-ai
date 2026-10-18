"""The project's members, issues and merge requests, read after the commits (story 8t).

A project member's tasks are the project's GitLab issues, and `23g` shows them
per member with what moved lately. Each GitLab harvest whose commit walk
finished reads, in this order and under the same ten-minute budget: the
members, the issues, the merge requests with the issues each relates to. What
it read travels on `HarvestResult.work` as one `WorkChange`; the merge into
what is stored is `pm_ai.domain.work.merge_work`, applied by the single writer.

## Paged by the filter

Issues and merge requests are listed oldest change first
(`order_by=updated_at&sort=asc&per_page=100`). After each page the next
request asks page 1 again, with `updated_after` set to the last `updated_at`
read — GitLab's "on or after" — skipping rows this walk already read (by iid
and `updated_at`, so an item updated mid-walk is read again rather than
skipped). Only when a whole page is rows already read does it ask the next
page of the same filter. An offset alone would shift under an item updated
mid-walk and skip the one behind it. Keyset pagination is never asked for.

## Positions

One per list: the last `updated_at` read. A first run (no position) lists
every open item of any age (`state=opened`) and nothing closed; one that
completes with nothing sets the position to the run's start less five minutes,
so the next run is incremental (`state=all&updated_after=…`). When the last
complete listing of the open set is more than a day old (or later than the
run's start: the clock moved back), a run lists `state=opened` completely after
its incremental walk; an item stored open, not read this run and missing from
it is recorded gone. A check cut short changes nothing and stops the run.

## Stops

A throttle, a server error, a refusal, the page cap or the budget stops the
run alike: the list being walked applies what it read and moves its position
to the last row read, the lists not started keep theirs, and the run's failure
names them. A 403 or 404 on a list is a section GitLab will not show — a
feature turned off, or not visible to the token: it is recorded unread with
its reason, keeps its rows, counts as complete for the run and is not a
failure. A 403 or 404 on one merge request's `related_issues` leaves its links
as they were, marked unread.

## Links

`GET /projects/:id/merge_requests/:iid/related_issues` for each merge request
read, in walk order, paged; an entry with no `project_id` or another
project's is dropped. At most `link_cap` link requests a run: at the cap the
merge-request position stops at the last merge request whose links were read,
and the rest are read next run.

## What is never kept

No description, no comment, no email, no field the domain values do not
name. A confidential issue keeps its iid, assignees, milestone and dates; its
title is a fixed phrase and its labels are left out — here, so its own title
never leaves this module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone

from pm_ai.connectors.gitlab.client import Page, PageUnavailable
from pm_ai.domain.handles import gitlab_handle
from pm_ai.domain.harvest import UNCLASSIFIED_FAULT_IS_RETRYABLE, HarvestFailure
from pm_ai.domain.work import (
    CONFIDENTIAL_ISSUE_TITLE,
    GitLabPerson,
    Issue,
    Member,
    MergeRequest,
    Section,
    SectionRead,
    WorkChange,
)

__all__ = [
    "FIRST_RUN_SLACK",
    "LINK_CAP",
    "OPEN_CHECK_EVERY",
    "WalkedWork",
    "WorkQuery",
    "WorkWalker",
]

LINK_CAP = 500
"""How many `related_issues` requests one run may make."""

OPEN_CHECK_EVERY = timedelta(hours=24)
"""How old the last complete listing of the open set may be before a run lists it again."""

FIRST_RUN_SLACK = timedelta(minutes=5)
"""What a first run that found nothing sets its position back from the run's start."""

MEMBERS = "members"
ISSUES = "issues"
MERGE_REQUESTS = "merge_requests"
RELATED_ISSUES = "related_issues"

_HIDDEN_STATUSES = frozenset({403, 404})

_LABEL = {MEMBERS: "members", ISSUES: "issues", MERGE_REQUESTS: "merge requests"}


@dataclass(frozen=True, slots=True)
class WorkQuery:
    """What one work-list page fetch asks for — the work seam's single argument.

    `resource` is `members`, `issues`, `merge_requests` or `related_issues`.
    `state` and `updated_after` filter the two item lists; `iid` names the
    merge request whose links are asked; `deadline` is when the run's one
    budget ends.
    """

    resource: str
    page: int
    state: str | None = None
    updated_after: datetime | None = None
    iid: int | None = None
    deadline: datetime | None = None


@dataclass(frozen=True, slots=True)
class WalkedWork:
    """What the work walk read, how it stopped, and where each list got to."""

    change: WorkChange
    failure: HarvestFailure | None
    issues: datetime | None
    merge_requests: datetime | None
    issues_checked: datetime | None
    merge_requests_checked: datetime | None


class _Stop(Exception):
    """The run stops: a throttle, a server error, a refusal or a bound."""

    def __init__(self, reason: str, *, retryable: bool, retry_after: timedelta | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.retry_after = retry_after


class _Hidden(Exception):
    """A 403 or 404 on a list: a section GitLab will not show."""

    def __init__(self, status: int | None) -> None:
        super().__init__(status)
        self.status = status


class _LinkCapReached(Exception):
    """The run's link requests are spent."""


@dataclass
class _ItemWalk:
    last: datetime | None = None
    complete: bool = False
    capped: bool = False
    stop: _Stop | None = None
    iids: set[int] = field(default_factory=set)


def _instant(value: object) -> datetime | None:
    """An ISO instant with an offset, as aware UTC; `None` for anything else."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _day(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    """An integer GitLab sent, never a boolean or text."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _item_key(row: object) -> tuple[int, datetime] | None:
    """An item row's iid and `updated_at`, or `None` for a row that cannot be placed."""
    if not isinstance(row, Mapping):
        return None
    iid = _integer(row.get("iid"))
    updated = _instant(row.get("updated_at"))
    if iid is None or updated is None:
        return None
    return iid, updated


@dataclass
class WorkWalker:
    """One run's walk of a project's members, issues and merge requests."""

    instance: str
    host: str
    project: str
    fetch: Callable[[WorkQuery], Page]
    now: Callable[[], datetime]
    started: datetime
    deadline: datetime
    page_cap: int
    link_cap: int = LINK_CAP
    _links_asked: int = 0

    # ── Mapping ──────────────────────────────────────────────────────────────

    def _person(self, raw: object) -> GitLabPerson | None:
        if not isinstance(raw, Mapping):
            return None
        bot = raw.get("bot")
        return GitLabPerson(
            handle=gitlab_handle(self.host, raw.get("id")),
            name=_text(raw.get("name")),
            username=_text(raw.get("username")),
            bot=bot if isinstance(bot, bool) else None,
        )

    def _member(self, raw: object) -> Member | None:
        person = self._person(raw)
        if person is None or not isinstance(raw, Mapping):
            return None
        return Member(person=person, state=_text(raw.get("state")))

    def _issue(self, row: Mapping[str, object], iid: int, updated: datetime) -> Issue | None:
        state = _text(row.get("state"))
        if state is None:
            return None
        raw_assignees = row.get("assignees")
        if not isinstance(raw_assignees, list):
            single = row.get("assignee")
            raw_assignees = [single] if isinstance(single, Mapping) else []
        assignees = tuple(
            person for person in (self._person(raw) for raw in raw_assignees) if person is not None
        )
        milestone = row.get("milestone")
        milestone_title = _text(milestone.get("title")) if isinstance(milestone, Mapping) else None
        milestone_due = _day(milestone.get("due_date")) if isinstance(milestone, Mapping) else None
        confidential = row.get("confidential") is True
        raw_labels = row.get("labels")
        labels = (
            tuple(label for label in raw_labels if isinstance(label, str))
            if isinstance(raw_labels, list)
            else ()
        )
        return Issue(
            iid=iid,
            # A confidential issue's own title and labels never leave this module.
            title=CONFIDENTIAL_ISSUE_TITLE if confidential else (_text(row.get("title")) or ""),
            state=state,
            assignees=assignees,
            milestone_title=milestone_title,
            milestone_due=milestone_due,
            labels=() if confidential else labels,
            updated_at=updated,
            closed_at=_instant(row.get("closed_at")),
            web_url=_text(row.get("web_url")),
            confidential=confidential,
        )

    def _merge_request(
        self, row: Mapping[str, object], iid: int, updated: datetime
    ) -> MergeRequest | None:
        state = _text(row.get("state"))
        if state is None:
            return None
        draft = row.get("draft")
        if not isinstance(draft, bool):
            older = row.get("work_in_progress")
            draft = older if isinstance(older, bool) else False
        return MergeRequest(
            iid=iid,
            title=_text(row.get("title")) or "",
            state=state,
            source_branch=_text(row.get("source_branch")),
            author=self._person(row.get("author")),
            draft=draft,
            updated_at=updated,
            merged_at=_instant(row.get("merged_at")),
            closed_at=_instant(row.get("closed_at")),
            web_url=_text(row.get("web_url")),
        )

    # ── Asking ───────────────────────────────────────────────────────────────

    def _ask(self, query: WorkQuery) -> Page:
        """One page, or `_Hidden` for a 403/404, or `_Stop` for everything else."""
        if self.now() >= self.deadline:
            raise _Stop(
                f"the harvest spent its fetch budget before page {query.page} of "
                f"the {query.resource.replace('_', ' ')} was asked",
                retryable=True,
            )
        asked = WorkQuery(
            resource=query.resource,
            page=query.page,
            state=query.state,
            updated_after=query.updated_after,
            iid=query.iid,
            deadline=self.deadline,
        )
        try:
            page = self.fetch(asked)
        except PageUnavailable as refused:
            if refused.status in _HIDDEN_STATUSES:
                raise _Hidden(refused.status) from None
            raise _Stop(
                refused.reason, retryable=refused.retryable, retry_after=refused.retry_after
            ) from None
        except Exception as unexpected:  # noqa: BLE001 — named by type, never by text
            raise _Stop(
                f"the transport raised {type(unexpected).__name__}, which pm-ai "
                f"cannot classify",
                retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
            ) from None
        if not isinstance(page, Page):
            raise _Stop(
                f"the transport answered with {type(page).__name__} rather than a "
                f"Page — a wiring fault in pm-ai rather than a provider fault",
                retryable=False,
            )
        return page

    @staticmethod
    def _advance(page: Page, number: int) -> int | None:
        """The next page number of an offset walk, `None` at the end, or `_Stop`."""
        if page.next_page is None:
            return None
        if page.next_page != number + 1:
            raise _Stop(
                f"GitLab said the page after {number} is {page.next_page}, which "
                f"does not follow it, so the walk is not reported complete",
                retryable=False,
            )
        return page.next_page

    def _walk_items(
        self,
        kind: str,
        *,
        state: str,
        after: datetime | None,
        accept: Callable[[Mapping[str, object], int, datetime], None],
    ) -> _ItemWalk:
        """One item list, paged by the filter. `_Hidden` propagates; a stop is kept."""
        walk = _ItemWalk()
        seen: set[tuple[int, datetime]] = set()
        filter_after = after
        number = 1
        asked = 0
        try:
            while True:
                if asked >= self.page_cap:
                    raise _Stop(
                        f"the {self.page_cap}-page cap was reached on the "
                        f"{_LABEL[kind]} list",
                        retryable=True,
                    )
                page = self._ask(
                    WorkQuery(kind, number, state=state, updated_after=filter_after)
                )
                asked += 1
                fresh = False
                for row in page.rows:
                    key = _item_key(row)
                    if key is None or key in seen:
                        continue
                    seen.add(key)
                    accept(row, key[0], key[1])  # a mapping: `_item_key` checked it
                    walk.iids.add(key[0])
                    walk.last = key[1] if walk.last is None else max(walk.last, key[1])
                    fresh = True
                following = self._advance(page, number)
                if following is None:
                    walk.complete = True
                    return walk
                if fresh:
                    filter_after = walk.last
                    number = 1
                else:
                    number = following
        except _Stop as stop:
            walk.stop = stop
            return walk
        except _LinkCapReached:
            walk.capped = True
            return walk

    # ── The sections ─────────────────────────────────────────────────────────

    def _members(self) -> tuple[Member, ...]:
        members: list[Member] = []
        number = 1
        asked = 0
        while True:
            if asked >= self.page_cap:
                raise _Stop(
                    f"the {self.page_cap}-page cap was reached on the member list",
                    retryable=True,
                )
            page = self._ask(WorkQuery(MEMBERS, number))
            asked += 1
            members.extend(m for m in (self._member(row) for row in page.rows) if m is not None)
            following = self._advance(page, number)
            if following is None:
                return tuple(members)
            number = following

    def _links(self, mr_project: int, iid: int) -> tuple[tuple[int, ...], frozenset[int]] | None:
        """This project's issues one merge request relates to, and which are confidential.

        `None` for a 403 or 404: the links are unread. `_LinkCapReached` when the
        run's link requests are spent before this merge request's are read.
        """
        linked: list[int] = []
        confidential: set[int] = set()
        number = 1
        while True:
            if self._links_asked >= self.link_cap:
                raise _LinkCapReached()
            self._links_asked += 1
            try:
                page = self._ask(WorkQuery(RELATED_ISSUES, number, iid=iid))
            except _Hidden:
                return None
            for row in page.rows:
                if not isinstance(row, Mapping):
                    continue
                project = _integer(row.get("project_id"))
                issue = _integer(row.get("iid"))
                if project is None or project != mr_project or issue is None:
                    continue
                if issue not in linked:
                    linked.append(issue)
                if row.get("confidential") is True:
                    confidential.add(issue)
            following = self._advance(page, number)
            if following is None:
                return tuple(linked), frozenset(confidential)
            number = following

    def _first_run_position(self, walk: _ItemWalk, stored: datetime | None) -> datetime | None:
        if walk.last is not None:
            return walk.last
        if stored is None and walk.complete:
            return self.started - FIRST_RUN_SLACK
        return stored

    def walk(
        self,
        *,
        issues: datetime | None,
        merge_requests: datetime | None,
        issues_checked: datetime | None,
        merge_requests_checked: datetime | None,
    ) -> WalkedWork:
        """Members, issues, merge requests with links — stopping as the module says."""
        sections: list[SectionRead] = []
        positions = {
            ISSUES: issues,
            MERGE_REQUESTS: merge_requests,
            f"{ISSUES}_checked": issues_checked,
            f"{MERGE_REQUESTS}_checked": merge_requests_checked,
        }
        members: tuple[Member, ...] | None = None
        read_issues: dict[int, Issue] = {}
        read_mrs: dict[int, MergeRequest] = {}
        open_sets: dict[str, frozenset[int] | None] = {ISSUES: None, MERGE_REQUESTS: None}
        confidential: set[int] = set()
        order = [MEMBERS, ISSUES, MERGE_REQUESTS]
        failure: HarvestFailure | None = None

        def stopped(kind: str, stop: _Stop) -> HarvestFailure:
            later = order[order.index(kind) + 1 :]
            not_read = (
                f" Not read this run: {', '.join(_LABEL[k] for k in later)}."
                if later
                else ""
            )
            return HarvestFailure(
                reason=(
                    f"{self.instance} stopped while reading {self.project!r}'s "
                    f"{_LABEL[kind]}: {stop.reason.rstrip('.')}. What was read of the "
                    f"{_LABEL[kind]} is kept and its position moved to the last "
                    f"row read.{not_read}"
                ),
                retryable=stop.retryable,
                retry_after=stop.retry_after,
            )

        def hidden(kind: str, status: int | None) -> SectionRead:
            return SectionRead(
                Section(kind),
                complete=False,
                reason=(
                    f"GitLab answered {status} for the {_LABEL[kind]} list: the "
                    f"feature is turned off, or not visible to the token. The "
                    f"rows read before are kept, and it is asked again next run."
                ),
            )

        def check_cut(kind: str, stop: _Stop) -> HarvestFailure:
            later = order[order.index(kind) + 1 :]
            not_read = (
                f" Not read this run: {', '.join(_LABEL[k] for k in later)}."
                if later
                else ""
            )
            return HarvestFailure(
                reason=(
                    f"{self.instance} stopped while checking which of "
                    f"{self.project!r}'s {_LABEL[kind]} are still open: "
                    f"{stop.reason.rstrip('.')}. The check changed nothing — no "
                    f"item was marked gone — and it is asked again next run; the "
                    f"{_LABEL[kind]} read before it are kept.{not_read}"
                ),
                retryable=stop.retryable,
                retry_after=stop.retry_after,
            )

        # ── Members, whole each run ──────────────────────────────────────────
        try:
            members = self._members()
            sections.append(SectionRead(Section.MEMBERS, complete=True))
        except _Hidden as refused:
            sections.append(hidden(MEMBERS, refused.status))
        except _Stop as cut:
            sections.append(SectionRead(Section.MEMBERS, complete=False, reason=cut.reason))
            failure = stopped(MEMBERS, cut)

        # ── Issues, then merge requests ──────────────────────────────────────
        def accept_issue(row: Mapping[str, object], iid: int, updated: datetime) -> None:
            issue = self._issue(row, iid, updated)
            if issue is not None:
                read_issues[iid] = issue

        def accept_mr(row: Mapping[str, object], iid: int, updated: datetime) -> None:
            mr = self._merge_request(row, iid, updated)
            if mr is None:
                return
            project = _integer(row.get("project_id"))
            # With no project id there is no telling which linked issues are
            # this project's: the links are unread, never read-and-empty.
            links = None if project is None else self._links(project, iid)
            if links is None:
                read_mrs[iid] = replace(mr, links_read=False)
                return
            linked, secret = links
            confidential.update(secret)
            read_mrs[iid] = replace(mr, issue_iids=linked)

        for kind, accept in ((ISSUES, accept_issue), (MERGE_REQUESTS, accept_mr)):
            if failure is not None:
                break
            stored = positions[kind]
            checked = positions[f"{kind}_checked"]
            first = stored is None
            try:
                walk = self._walk_items(
                    kind,
                    state="opened" if first else "all",
                    after=stored,
                    accept=accept,
                )
            except _Hidden as refused:
                sections.append(hidden(kind, refused.status))
                continue
            positions[kind] = self._first_run_position(walk, stored)
            if first and walk.complete:
                # A first run's walk is itself a complete listing of the open set.
                open_sets[kind] = frozenset(walk.iids)
                positions[f"{kind}_checked"] = self.started
            if walk.complete:
                sections.append(SectionRead(Section(kind), complete=True))
            elif walk.capped:
                sections.append(
                    SectionRead(
                        Section(kind),
                        complete=False,
                        reason=(
                            f"the {self.link_cap}-request cap on merge-request "
                            f"links was reached; the merge requests after the "
                            f"last one linked are read next run"
                        ),
                    )
                )
                continue
            else:
                halt = walk.stop
                if halt is None:  # pragma: no cover — a walk ends one of three ways
                    halt = _Stop("the walk ended without finishing", retryable=False)
                sections.append(SectionRead(Section(kind), complete=False, reason=halt.reason))
                failure = stopped(kind, halt)
                break
            # The daily check of the open set, after the incremental walk so a
            # check that cannot finish never holds the walk back. A stale or
            # future-dated check instant (the clock moved back) is checked.
            if first or not (
                checked is None
                or checked > self.started
                or self.started - checked > OPEN_CHECK_EVERY
            ):
                continue
            try:
                listing = self._walk_items(
                    kind, state="opened", after=None, accept=lambda row, iid, at: None
                )
            except _Hidden as refused:
                # The incremental rows stand; the section says the list is hidden.
                sections[-1] = hidden(kind, refused.status)
                continue
            if listing.stop is not None:
                # A check cut short changes nothing: no item is judged gone and
                # the check instant does not move.
                failure = check_cut(kind, listing.stop)
                break
            open_sets[kind] = frozenset(listing.iids)
            positions[f"{kind}_checked"] = self.started

        return WalkedWork(
            change=WorkChange(
                instance=self.instance,
                started_at=self.started,
                members=members,
                issues=tuple(read_issues[iid] for iid in sorted(read_issues)),
                merge_requests=tuple(read_mrs[iid] for iid in sorted(read_mrs)),
                open_issue_iids=open_sets[ISSUES],
                open_merge_request_iids=open_sets[MERGE_REQUESTS],
                confidential_issue_iids=frozenset(confidential),
                sections=tuple(sections),
            ),
            failure=failure,
            issues=positions[ISSUES],
            merge_requests=positions[MERGE_REQUESTS],
            issues_checked=positions[f"{ISSUES}_checked"],
            merge_requests_checked=positions[f"{MERGE_REQUESTS}_checked"],
        )

