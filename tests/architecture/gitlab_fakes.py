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

from datetime import datetime
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
) -> Pages | Any:
    """Give a composed daemon's GitLab connector a token and a page fetch.

    `rows` become one page; `fetch` replaces the page seam outright. Returns the
    fetch installed, so a test can read what it was asked.
    """
    connector = daemon.connectors[instance]
    connector.credential = TOKEN
    installed = fetch if fetch is not None else Pages(tuple(rows or ()))
    connector.fetch_page = installed
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


__all__ = ["TOKEN", "Pages", "PageUnavailable", "Wire", "WireOverrun", "commit", "install", "page"]
