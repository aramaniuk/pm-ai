"""Stand-ins for GitLab, at the page seam and at the wire (story 8n).

The connector has no in-memory list any more — a default that walks a fake is
the defect 8n ended — so every test that harvests says what GitLab answered.
Two seams, two fakes:

- `Pages` stands in for `GitLabConnectorAdapter.fetch_page`: whole pages, and
  the queries the harvest made, so a test can assert the `since` a run asked
  from.
- `Wire` stands in for `GitLabConnectorAdapter.transport`: recorded HTTP
  answers, and the requests that were sent, so a test composed through
  `build()` can assert the host and the query of what went out.

Neither opens a socket. Here rather than beside the connector tests because
the slice tests use them too, and `tests/architecture` is the directory on
pytest's import path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pm_ai.connectors.gitlab import CommitQuery, Page, PageUnavailable
from pm_ai.connectors.gitlab.client import GitLabRequest, GitLabResponse

TOKEN = "glpat-test-token-not-real"
"""A token for tests: a value of the right shape that no GitLab would accept."""


def commit(
    sha: str,
    message: str | None = "a commit",
    *,
    at: datetime | str | None,
    email: str | None = "alex@example.com",
) -> dict[str, Any]:
    """One row in GitLab's list-commits shape: `id`, `message`, `author_email`,
    `committed_date` as the ISO string GitLab sends."""
    row: dict[str, Any] = {
        "id": sha,
        "message": message,
        "committed_date": at.isoformat() if isinstance(at, datetime) else at,
    }
    if email is not None:
        row["author_email"] = email
    return row


class Pages:
    """A page fetch handing back exactly these pages, then optionally refusing.

    Pages are numbered from 1, as GitLab numbers them. The last supplied page
    ends the walk when nothing is set to fail, and points at one more page when
    something is: that next fetch is where `fails_at` is raised.
    """

    def __init__(self, *pages: tuple[dict, ...], fails_at: Exception | None = None) -> None:
        self.pages = pages
        self.fails_at = fails_at
        self.asked: list[CommitQuery] = []

    def __call__(self, query: CommitQuery) -> Page:
        self.asked.append(query)
        index = query.page - 1
        if 0 <= index < len(self.pages):
            last = index == len(self.pages) - 1
            return Page(
                rows=tuple(self.pages[index]),
                next_page=None if last and self.fails_at is None else query.page + 1,
            )
        if self.fails_at is not None:
            raise self.fails_at
        return Page()

    @property
    def since(self) -> list[datetime]:
        """The instant each fetch asked from."""
        return [query.since for query in self.asked]


def install(
    daemon: Any,
    rows: list[dict] | tuple[dict, ...] | None = None,
    *,
    instance: str = "gitlab:alpha",
    fetch: Any = None,
    work: Any = None,
) -> Pages | Any:
    """Give a composed daemon's GitLab connector a token and a page fetch.

    `rows` become one page; `fetch` replaces the page seam outright. Returns the
    fetch installed, so a test can read what it was asked.
    """
    connector = daemon.connectors[instance]
    connector.credential = TOKEN
    installed = fetch if fetch is not None else Pages(tuple(rows or ()))
    connector.fetch_page = installed
    # Story 8t: a composed connector also reads the project's work, through
    # `work` when one is given; a commit seam with no work seam reads commits
    # alone.
    connector.fetch_work = work
    return installed


class WireOverrun(BaseException):
    """A request the fixture held no answer for.

    A `BaseException` on purpose. The client turns anything a transport raises
    into a classified `PageUnavailable`, and the harvest and the health check
    turn every `Exception` into a value — so an `AssertionError` raised here
    became an ordinary failure the test could pass over. This one is caught by
    none of them and fails the test where the extra request was sent.
    """


class Wire:
    """A transport answering from a list, recording every request sent.

    An answer is a `GitLabResponse` or an exception to raise. Asking for more
    answers than the list holds sets `overrun` and raises `WireOverrun` — an
    extra request is a retry that should not have happened.
    """

    def __init__(self, *answers: GitLabResponse | Exception) -> None:
        self.answers = list(answers)
        self.sent: list[GitLabRequest] = []
        self.overrun = False

    def __call__(self, request: GitLabRequest) -> GitLabResponse:
        self.sent.append(request)
        if not self.answers:
            self.overrun = True
            raise WireOverrun(f"an unexpected request was sent: {request!r}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def query(self, index: int = 0) -> dict[str, list[str]]:
        """The decoded query string of one sent request."""
        return parse_qs(urlsplit(self.sent[index].url).query)


def page(*rows: dict, next_page: int | str | None = None, status: int = 200) -> GitLabResponse:
    """A commit-list answer, with GitLab's next-page header when one is given."""
    headers = {"Content-Type": "application/json"}
    headers["X-Next-Page"] = "" if next_page is None else str(next_page)
    return GitLabResponse(status=status, body=list(rows), headers=headers)


# ── Story 8t: a GitLab that answers by what is asked ─────────────────────────


def stamp(at: datetime) -> str:
    """An instant as GitLab writes one: milliseconds and `Z`."""
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def person(user_id: Any, name: str = "Dana Smith", username: str = "dana", **extra: Any) -> dict[str, Any]:
    """A user object as GitLab nests one: `id`, `name`, `username`, and whatever else."""
    return {"id": user_id, "name": name, "username": username, **extra}


def issue(iid: int, updated: datetime, state: str = "opened", **extra: Any) -> dict[str, Any]:
    """One row in GitLab's issue-list shape, a description included (never kept)."""
    row: dict[str, Any] = {
        "id": 10_000 + iid,
        "iid": iid,
        "project_id": PROJECT_ID,
        "title": f"issue {iid}",
        "description": f"the body of issue {iid}, which pm-ai never keeps",
        "state": state,
        "assignees": [],
        "labels": [],
        "milestone": None,
        "updated_at": stamp(updated),
        "closed_at": None,
        "web_url": f"https://gitlab.example.com/acme/web/-/issues/{iid}",
        "confidential": False,
    }
    row.update(extra)
    return row


def merge_request(iid: int, updated: datetime, state: str = "opened", **extra: Any) -> dict[str, Any]:
    """One row in GitLab's merge-request-list shape."""
    row: dict[str, Any] = {
        "id": 20_000 + iid,
        "iid": iid,
        "project_id": PROJECT_ID,
        "title": f"merge request {iid}",
        "description": f"the body of merge request {iid}, which pm-ai never keeps",
        "state": state,
        "source_branch": f"{iid}-a-branch",
        "author": person(17),
        "draft": False,
        "updated_at": stamp(updated),
        "merged_at": None,
        "closed_at": None,
        "web_url": f"https://gitlab.example.com/acme/web/-/merge_requests/{iid}",
    }
    row.update(extra)
    return row


PROJECT_ID = 7
"""The numeric id GitLab gives the fake's project."""


class GitLab:
    """A transport answering like one GitLab project, recording every request.

    Lists are filtered and ordered the way the connector asks GitLab to:
    `state`, `updated_after` (on or after), oldest `updated_at` first, 100 a
    page, with GitLab's next-page header. `refuse` maps a list — `members`,
    `issues`, `merge_requests`, `related_issues`, or `related_issues:<iid>` —
    to the answer given instead. `before(resource, query)` runs before each
    answer, so a test can change the project mid-walk.
    """

    PER_PAGE = 100

    def __init__(self) -> None:
        self.commits: list[dict[str, Any]] = []
        self.members: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        self.merge_requests: list[dict[str, Any]] = []
        self.related: dict[int, list[dict[str, Any]]] = {}
        self.refuse: dict[str, GitLabResponse] = {}
        self.before: Any = None
        self.sent: list[str] = []

    def asked(self, resource: str) -> list[dict[str, list[str]]]:
        """The decoded queries of every request sent for one list."""
        return [
            parse_qs(urlsplit(url).query)
            for url in self.sent
            if self._resource(urlsplit(url).path)[0] == resource
        ]

    @staticmethod
    def _resource(path: str) -> tuple[str, int | None]:
        tail = path.split("/projects/", 1)[1].split("/", 1)[1]
        if tail == "repository/commits":
            return "commits", None
        if tail == "members/all":
            return "members", None
        if tail.startswith("merge_requests/") and tail.endswith("/related_issues"):
            return "related_issues", int(tail.split("/")[1])
        return tail, None

    def __call__(self, request: GitLabRequest) -> GitLabResponse:
        self.sent.append(request.url)
        parts = urlsplit(request.url)
        resource, iid = self._resource(parts.path)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        if self.before is not None:
            self.before(resource, query)
        for key in (f"{resource}:{iid}", resource):
            if key in self.refuse:
                return self.refuse[key]
        number = int(query.get("page", "1"))
        if resource == "commits":
            return page(*self.commits)
        if resource == "members":
            rows = list(self.members)
        elif resource == "related_issues":
            rows = list(self.related.get(iid or 0, []))
        else:
            rows = list(self.issues if resource == "issues" else self.merge_requests)
            state = query.get("state", "all")
            if state != "all":
                rows = [row for row in rows if row["state"] == state]
            after = query.get("updated_after")
            if after is not None:
                floor = datetime.fromisoformat(after)
                rows = [row for row in rows if datetime.fromisoformat(row["updated_at"]) >= floor]
            rows.sort(key=lambda row: (row["updated_at"], row["iid"]))
        start = (number - 1) * self.PER_PAGE
        chunk = rows[start : start + self.PER_PAGE]
        following = number + 1 if start + self.PER_PAGE < len(rows) else None
        return page(*chunk, next_page=following)


def refused(status: int, **headers: str) -> GitLabResponse:
    """An error answer with GitLab's JSON message body."""
    return GitLabResponse(status=status, body={"message": f"{status}"}, headers=dict(headers))


__all__ = [
    "PROJECT_ID",
    "TOKEN",
    "GitLab",
    "Pages",
    "PageUnavailable",
    "Wire",
    "WireOverrun",
    "commit",
    "install",
    "issue",
    "merge_request",
    "page",
    "person",
    "refused",
    "stamp",
]
