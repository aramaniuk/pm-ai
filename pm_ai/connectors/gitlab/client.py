"""The GitLab HTTP client: one project's commits, read-only (story 8n), and
its members, issues and merge requests (story 8t).

The connector around this already had paging, the cursor, the coverage rules
and the mapping; what it lacked was the wire. This module is that wire and
nothing else — one `GET` to GitLab's list-commits call
(https://docs.gitlab.com/api/commits/), followed through GitLab's own
next-page header, and the one-commit request the health check makes.

## Read-only, class H egress (AD-1)

`GET` and nothing else. There is no method parameter, so a write is not a flag
away. No branch is named and the all-branches option is never sent: the list is
the default branch's, and work reaches it by merge.

## Where the token goes

In the `PRIVATE-TOKEN` header GitLab documents for personal access tokens
(https://docs.gitlab.com/api/rest/authentication/), to the configured host and
nowhere else. A redirect is refused, not followed — `urllib`'s default opener
copies every header onto the `Location`, which would hand the token to whatever
the redirect names before any check here could run. The same reasoning, and
the same refusing opener, as `pm_ai.connectors.graph.client`; copied rather
than imported because adapters do not import one another.

Nothing is sent before three checks pass, each refused without a socket: the
host is `https` with no query, fragment, user info or trailing slash; the
project path is one GitLab can be asked for; and the token can be a header
value at all.

## What a failure sentence may carry

Statuses and this client's own words. Never a response body, never an
exception's text — a `urllib` or SSL message names the URL it failed on — and
never the token. The sentence becomes a `HarvestFailure.reason`, which is
written to Tier 2 and read back into reports.

## Throttling

A 429's `Retry-After`, in seconds
(https://docs.gitlab.com/administration/settings/user_and_ip_rate_limits/), is
waited out inside the fetch when it fits the run's remaining budget, through a
`wait` the composition root sets to `time.sleep`. No hint, or a date for one,
waits `DEFAULT_THROTTLE_BACKOFF`. A hint that does not fit, a third throttle on
one page, or a client composed with no way to wait ends the walk with the hint
on the failure, retryable — when to come back is the daemon's decision (AD-9).
The health check never waits.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from pm_ai.domain.harvest import UNCLASSIFIED_FAULT_IS_RETRYABLE

__all__ = [
    "API_PATH",
    "DEFAULT_HOST",
    "DEFAULT_THROTTLE_BACKOFF",
    "FETCH_BUDGET",
    "GitLabClient",
    "MAX_BODY_BYTES",
    "MAX_HEADER_DIGITS",
    "GitLabRequest",
    "GitLabResponse",
    "NEXT_PAGE_HEADER",
    "PER_PAGE",
    "Page",
    "PageUnavailable",
    "RETRY_AFTER_HEADER",
    "SOCKET_TIMEOUT_SECONDS",
    "THROTTLE_RETRIES",
    "TOKEN_HEADER",
    "USER_AGENT",
    "host_refusal",
    "project_refusal",
    "render_instant",
    "token_refusal",
]


# ── Where GitLab is, and the bounds this client holds itself to ──────────────

DEFAULT_HOST = "https://gitlab.com"
"""The host a row that names none is read from — the one defaulted row key."""

API_PATH = "/api/v4"
"""The REST API's root beneath the host, so `https://git.internal/gitlab` — a
self-managed instance under a path — is asked at `…/gitlab/api/v4/…`."""

TOKEN_HEADER = "PRIVATE-TOKEN"
USER_AGENT = "pm-ai"
"""Sent on every request, so a GitLab administrator reading their logs can see
what is asking."""
NEXT_PAGE_HEADER = "x-next-page"
RETRY_AFTER_HEADER = "Retry-After"

PER_PAGE = 100
"""Rows a page. GitLab's maximum for `per_page`, so a walk asks as few times as
it can."""

SOCKET_TIMEOUT_SECONDS = 30
"""Each request's socket timeout — the Graph client's value, for its reason: a
host that accepts the connection and never answers must not hold a harvest."""

DEFAULT_THROTTLE_BACKOFF = timedelta(seconds=30)
"""What a 429 waits when it carried no number of seconds — absent, or a date."""

THROTTLE_RETRIES = 2
"""How many times one page is re-asked after a 429. The third throttle ends the
walk, with the hint on the failure."""

FETCH_BUDGET = timedelta(minutes=10)
"""How long one whole harvest may keep asking, wall-clock.

Chosen against CAP-2's 240-minute cycle, so a fetch cannot outlive its own
cycle and overlap the next — `GraphClient.budget`'s value and reasoning. The
connector measures it from the instant the first page came back; a throttle
wait is honoured only when it fits what is left of it.
"""

MAX_BODY_BYTES = 16 * 1024 * 1024
"""The most of one response body this client reads. A page of 100 commits is a
few hundred kilobytes; a body past this is not a commit page, and reading it
whole would let the provider decide how much memory a harvest takes."""

MAX_HEADER_DIGITS = 9
"""The longest digit string a `Retry-After` or `x-next-page` value is read as a
number from. Longer ones never reach `int()`, which refuses past 4300 digits
with a `ValueError` — a header value must not be able to raise."""

_LONGEST_HINT = timedelta(days=3660)
"""The cap on a stated `Retry-After`, so an absurd number of seconds cannot
overflow `timedelta`. Far past any budget, so it is refused either way."""

_DIGITS = re.compile(r"[0-9]+")


def render_instant(at: datetime) -> str:
    """An aware instant as GitLab's `since` and the cursor spell it: `…Z`.

    Never `+00:00`: a literal `+` in a query string reads as a space.
    """
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── What a request may be sent with ──────────────────────────────────────────


def host_refusal(value: object) -> str | None:
    """Why `value` cannot be the GitLab host, or `None` when it can.

    https, a host name, an optional path, and nothing else: no query, fragment
    or user info, and no trailing slash, because the API path is appended to it
    as written.
    """
    if not isinstance(value, str) or not value:
        return f"it is {value!r}, not a URL such as {DEFAULT_HOST!r}"
    if value != value.strip() or any(character.isspace() for character in value):
        return "it holds whitespace"
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        parts.port  # noqa: B018 — raises for a port that is not a number
    except ValueError:
        return "it is not a URL pm-ai can read"
    if parts.scheme != "https":
        return (
            f"its scheme is {parts.scheme or 'missing'!r}; the token is only "
            f"sent over https"
        )
    if not hostname:
        return "it names no host"
    if "@" in parts.netloc:
        return "it carries user info, and the only credential sent is the token header"
    if parts.query or "?" in value:
        return "it carries a query"
    if parts.fragment or "#" in value:
        return "it carries a fragment"
    if value.endswith("/"):
        return "it ends in '/'; the API path is appended to it as written"
    return None


def project_refusal(value: object) -> str | None:
    """Why `value` cannot be asked for as a GitLab project path, or `None`.

    Non-blank, no leading `/`, and no `?`, `#` or whitespace — each would turn
    the path into a different request once it sits in a URL.
    """
    if not isinstance(value, str) or not value.strip():
        return f"it is {value!r}, not a project path such as 'group/project'"
    if value.startswith("/"):
        return "it starts with '/'"
    for character, named in (("?", "'?'"), ("#", "'#'")):
        if character in value:
            return f"it holds {named}"
    if any(character.isspace() for character in value):
        return "it holds whitespace"
    return None


def token_refusal(token: object) -> str | None:
    """Why `token` cannot travel in a header, or `None` when it can.

    Visible ASCII only. A newline would end the header and start another; a
    non-ASCII character is not a header byte at all. The token itself is never
    quoted in the answer.
    """
    if not isinstance(token, str) or not token:
        return "no token is set"
    for character in token:
        if character in "\r\n":
            return "the stored token holds a line break, which cannot be a header value"
        if not 0x21 <= ord(character) <= 0x7E:
            return (
                "the stored token holds a space, a control character or a "
                "non-ASCII character, which cannot be a header value"
            )
    return None


# ── Refusals ─────────────────────────────────────────────────────────────────


class PageUnavailable(Exception):
    """A page could not be fetched, carrying what the caller must decide.

    The retry semantics ride on the exception rather than inside its message,
    because `harvest` converts this into a `HarvestFailure` and "wait 90
    seconds" (a 429 with `Retry-After`) is a different instruction to an
    operator than "this token is dead".

    Never escapes `harvest`. A raise cannot carry the coverage a partial fetch
    already earned, which is exactly why the outcome is a value.

    `reason` must carry no credential material. It becomes a `HarvestFailure`,
    which is persisted in Tier 2 and never rebuilt, so every sentence here is
    composed from a status and this client's own words.

    `status` is the HTTP status that produced it, when one did, so the health
    check can name a remedy per status without parsing the sentence.
    """

    def __init__(
        self,
        reason: str,
        *,
        retryable: bool,
        retry_after: timedelta | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.retry_after = retry_after
        self.status = status


@dataclass(frozen=True, slots=True)
class Page:
    """One provider response: the rows it carried, and the page after it.

    `next_page` is `None` on the last page, stated rather than inferred from a
    short one — GitLab's own next-page header decides, and an empty header is
    the last page.
    """

    rows: tuple[Any, ...] = ()
    next_page: int | None = None


# ── The wire, as a value ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GitLabRequest:
    """One outbound `GET`, recorded rather than performed.

    `headers` carries the token, so `__repr__` names the headers and never their
    values — a pytest diff or a traceback is otherwise a token on disk.
    """

    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float = SOCKET_TIMEOUT_SECONDS
    """The socket timeout for this request: thirty seconds, or less when the
    run's budget has less left, so the last request cannot overrun it."""

    def __repr__(self) -> str:
        return f"GitLabRequest(url={self.url!r}, headers={sorted(self.headers)!r})"


@dataclass(frozen=True, slots=True)
class GitLabResponse:
    """What came back: a status, the decoded JSON body, and the headers.

    `body` is whatever the JSON decoded to — a list for a commit page, an object
    for most errors — or `None` for a body that is not JSON at all.
    """

    status: int
    body: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        """One header, matched case-insensitively — `urllib` and a fixture differ."""
        folded = name.casefold()
        for key, value in self.headers.items():
            if key.casefold() == folded:
                return value
        return None


def _no_redirect_opener() -> Any:
    """A `urllib` opener that declines every redirect rather than following it.

    `HTTPRedirectHandler.redirect_request` returning `None` is `urllib`'s own
    way to decline: no handler answers the 3xx, so it is raised as an
    `HTTPError` carrying the redirect status, which `_urllib_transport` refuses.
    """
    import urllib.request

    class RefusesRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *asked: Any, **named: Any) -> None:
            return None

    return urllib.request.build_opener(RefusesRedirects())


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return (parts.scheme, (parts.hostname or "").casefold(), parts.port)


def _path_only(url: str) -> str:
    """A URL as it may appear in a refusal: scheme, host and path, never the query."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}{parts.path}"


def _read_capped(source: Any, url: str) -> bytes:
    """A response body, read to at most `MAX_BODY_BYTES`, or a refusal.

    The read is guarded as the open is: a connection reset or a timeout while
    reading is a request that got no answer — retryable, named by type — and a
    body past the cap is refused rather than read whole.
    """
    try:
        raw = source.read(MAX_BODY_BYTES + 1)
    except Exception as silent:  # noqa: BLE001 — the type is named, never the text
        raise PageUnavailable(
            f"reading GitLab's answer to {_path_only(url)} failed "
            f"({type(silent).__name__}). Nothing was learned, which is not the "
            f"same as there being nothing to learn.",
            retryable=True,
        ) from silent
    if len(raw) > MAX_BODY_BYTES:
        raise PageUnavailable(
            f"GitLab's answer to {_path_only(url)} is larger than the "
            f"{MAX_BODY_BYTES}-byte cap this client reads, so it was not read.",
            retryable=True,
        )
    return bytes(raw)


def _decoded(raw: bytes) -> Any:
    """The JSON a body holds, or `None` for one that holds none."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _urllib_transport(request: GitLabRequest) -> GitLabResponse:
    """The real transport: one `GET` over stdlib HTTP, imported at call time.

    The standard library, as the Graph client uses, because it adds no
    dependency. `.importlinter` cannot name `urllib.request` (it refuses a
    subpackage of an external package), so the confinement of this second
    stdlib client to `pm_ai.connectors` is recorded in `deferred-work.md`
    rather than enforced.

    An HTTP error status is an *answer*, returned as the status it carries. A
    redirect is refused, not followed. Anything else raised out of the opener is
    a request that never got an answer — retryable, named by type alone.
    """
    import urllib.error
    import urllib.request

    outbound = urllib.request.Request(  # noqa: S310 — https-only, see below
        request.url, headers=dict(request.headers), method="GET"
    )
    if outbound.type != "https":
        raise PageUnavailable(
            f"refusing to send a GitLab request over {outbound.type!r}: the "
            f"token is only sent over https.",
            retryable=False,
        )
    try:
        with _no_redirect_opener().open(  # noqa: S310
            outbound, timeout=request.timeout
        ) as answer:
            landed = str(getattr(answer, "url", "") or request.url)
            if _origin(landed) != _origin(request.url):
                raise PageUnavailable(
                    f"the GitLab request to {_path_only(request.url)} was "
                    f"answered from {_path_only(landed)}, a different origin "
                    f"from the one the token was sent to. Refusing the answer "
                    f"rather than reading it.",
                    retryable=False,
                )
            return GitLabResponse(
                status=int(answer.status),
                body=_decoded(_read_capped(answer, request.url)),
                headers={key: value for key, value in answer.headers.items()},
            )
    except urllib.error.HTTPError as answered:
        if 300 <= int(answered.code) < 400:
            raise PageUnavailable(
                f"GitLab answered {answered.code} for {_path_only(request.url)} "
                f"with a redirect, and this client does not follow one: the "
                f"token header would travel to whatever the redirect names. "
                f"Check the row's host — it may need its https form or its "
                f"full path.",
                retryable=False,
                status=int(answered.code),
            ) from answered
        return GitLabResponse(
            status=int(answered.code),
            body=_decoded(_read_capped(answered, request.url)),
            headers={key: value for key, value in answered.headers.items()},
        )
    except PageUnavailable:
        raise
    except Exception as silent:  # noqa: BLE001 — the type is named, never the text
        raise PageUnavailable(
            f"the GitLab request to {_path_only(request.url)} got no answer "
            f"({type(silent).__name__}). Nothing was learned, which is not "
            f"the same as there being nothing to learn.",
            retryable=True,
        ) from silent


def _does_not_wait(seconds: float) -> None:
    """The default `GitLabClient.wait`: it returns without waiting, by name.

    Recognised by identity in the 429 branch, which refuses rather than
    re-asking a rate-limited provider at once. The composition root supplies
    `time.sleep`.
    """
    return None


# ── The client ───────────────────────────────────────────────────────────────


@dataclass
class GitLabClient:
    """One read-only reach into one GitLab project's commits.

    Holds no cursor and no schedule. Asked for one page, it answers with that
    page or raises `PageUnavailable`; the walk, the cursor and the coverage are
    the connector's.
    """

    host: str
    project: str
    token: str
    transport: Callable[[GitLabRequest], GitLabResponse] = _urllib_transport
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    wait: Callable[[float], None] = _does_not_wait
    budget: timedelta = FETCH_BUDGET
    throttle_retries: int = THROTTLE_RETRIES
    default_backoff: timedelta = DEFAULT_THROTTLE_BACKOFF

    # ── URLs ─────────────────────────────────────────────────────────────────

    @property
    def _project_root(self) -> str:
        """The project, its path URL-encoded as one segment.

        `group/project` is sent as `group%2Fproject`, GitLab's namespaced-path
        form (https://docs.gitlab.com/api/rest/ "Namespaced paths").
        """
        return f"{self.host}{API_PATH}/projects/{quote(self.project, safe='')}"

    @property
    def _commits_root(self) -> str:
        """The project's commits."""
        return f"{self._project_root}/repository/commits"

    def commits_url(self, since: datetime, page: int) -> str:
        """One page of the default branch's commits made at or after `since`."""
        return (
            f"{self._commits_root}?since={quote(render_instant(since), safe=':')}"
            f"&per_page={PER_PAGE}&page={page}"
        )

    def health_url(self) -> str:
        """The one commit the health check asks for."""
        return f"{self._commits_root}?per_page=1"

    # Story 8t — the project's members, issues and merge requests, and the
    # issues each merge request relates to. All `GET`, all offset-paged by
    # GitLab's next-page header; keyset pagination is never asked for.

    def members_url(self, page: int) -> str:
        """One page of the project's members, inherited ones included."""
        return f"{self._project_root}/members/all?per_page={PER_PAGE}&page={page}"

    def items_url(
        self, kind: str, *, state: str, updated_after: datetime | None, page: int
    ) -> str:
        """One page of the project's `issues` or `merge_requests`, oldest change first.

        `updated_after` is GitLab's "on or after", so a walk that asks again
        from the last instant it read re-reads that instant rather than
        skipping it. Absent on a first run and on the daily open check.
        """
        if kind not in ("issues", "merge_requests"):
            raise ValueError(f"{kind!r} is not a list this client reads")
        url = (
            f"{self._project_root}/{kind}?order_by=updated_at&sort=asc"
            f"&state={state}&per_page={PER_PAGE}&page={page}"
        )
        if updated_after is not None:
            url += f"&updated_after={quote(render_instant(updated_after), safe=':')}"
        return url

    def related_issues_url(self, iid: int, page: int) -> str:
        """One page of the issues one merge request relates to."""
        return (
            f"{self._project_root}/merge_requests/{int(iid)}/related_issues"
            f"?per_page={PER_PAGE}&page={page}"
        )

    # ── Asking ───────────────────────────────────────────────────────────────

    def commits(self, since: datetime, page: int, *, deadline: datetime | None = None) -> Page:
        """One page of commits, or `PageUnavailable` saying why not.

        A 200 whose body is not a list, and a next-page header that is not a
        page number, are refusals rather than guesses: neither says what is on
        the default branch. A missing or blank header is the last page.
        """
        return self.list_page(
            self.commits_url(since, page),
            page=page,
            what="GitLab commit list",
            deadline=deadline,
        )

    def list_page(
        self, url: str, *, page: int, what: str, deadline: datetime | None = None
    ) -> Page:
        """One page of any list this client reads, or `PageUnavailable`.

        The rules `commits` has always applied, for every list: a body that is
        not a list is refused rather than read as empty, and a next-page header
        that is not a page number ends the walk as not complete.
        """
        response = self._answer(url, deadline=deadline)
        body = response.body
        if not isinstance(body, list):
            raise PageUnavailable(
                f"GitLab answered {response.status} for page {page} of "
                f"{self.project!r} with a body that is not a {what}. "
                f"Refusing rather than reading it as an empty page.",
                retryable=False,
                status=response.status,
            )
        stated = response.header(NEXT_PAGE_HEADER)
        if stated is None or not stated.strip():
            return Page(rows=tuple(body), next_page=None)
        if not _DIGITS.fullmatch(stated.strip()) or len(stated.strip()) > MAX_HEADER_DIGITS:
            raise PageUnavailable(
                f"GitLab's next-page header after page {page} of "
                f"{self.project!r} is not a page number, so there is no telling "
                f"where the list continues. The pages before it are kept; the "
                f"walk is not reported complete.",
                retryable=False,
                status=response.status,
            )
        return Page(rows=tuple(body), next_page=int(stated.strip()))

    def check(self) -> None:
        """Ask for one commit; return when GitLab answers 2xx, raise otherwise.

        Never waits out a throttle: a health check is bounded at ten seconds by
        the registry, and a hint is reported instead.
        """
        self._answer(self.health_url(), deadline=None, honour_throttle=False)

    # ── The parts every request leans on ─────────────────────────────────────

    def _preflight(self) -> None:
        """The three refusals that come before any socket."""
        for refusal, named in (
            (host_refusal(self.host), f"the GitLab host {self.host!r}"),
            (project_refusal(self.project), "the GitLab project path"),
        ):
            if refusal is not None:
                raise PageUnavailable(
                    f"{named} cannot be used: {refusal}. Nothing was sent.",
                    retryable=False,
                )
        token = token_refusal(self.token)
        if token is not None:
            raise PageUnavailable(
                f"{token}; nothing was sent. Enrol the token again.",
                retryable=False,
            )

    def _answer(
        self, url: str, *, deadline: datetime | None, honour_throttle: bool = True
    ) -> GitLabResponse:
        """One request with the status dispatch around it; 2xx is returned."""
        self._preflight()
        limit = deadline if deadline is not None else self.now() + self.budget
        throttled = 0
        where = f"{self.project!r} on {self.host}"
        while True:
            # One reading: whether any budget is left, and how long this
            # request's socket may wait, so the last request cannot overrun it.
            left = (limit - self.now()).total_seconds()
            if left <= 0:
                raise PageUnavailable(
                    f"the fetch spent its {self.budget} budget before "
                    f"{where} was asked again. What was already walked is kept; "
                    f"the rest is the next run's.",
                    retryable=True,
                )
            response = self._send(url, timeout=min(float(SOCKET_TIMEOUT_SECONDS), left))
            status = response.status

            if 200 <= status < 300:
                return response
            if status == 401:
                raise PageUnavailable(
                    f"GitLab refused the token for {where} (401): it is "
                    f"expired, revoked or mistyped.",
                    retryable=False,
                    status=401,
                )
            if status == 403:
                raise PageUnavailable(
                    f"GitLab refused {where} with 403: the token lacks the "
                    f"`read_api` scope.",
                    retryable=False,
                    status=403,
                )
            if status == 404:
                raise PageUnavailable(
                    f"GitLab answered 404: no project is visible at "
                    f"{self.project!r} on {self.host}. GitLab answers 404 for a "
                    f"project the token cannot see as well as for one that does "
                    f"not exist.",
                    retryable=False,
                    status=404,
                )
            if status == 429:
                hint, source = self._retry_after(response)
                if not honour_throttle:
                    raise PageUnavailable(
                        f"GitLab throttled {where} (429) and asked for {hint} "
                        f"({source}); a health check does not wait.",
                        retryable=True,
                        retry_after=hint,
                        status=429,
                    )
                remaining = limit - self.now()
                if hint > remaining:
                    raise PageUnavailable(
                        f"GitLab throttled {where} (429) and asked for {hint} "
                        f"({source}), more than the {remaining} left of this "
                        f"run. Returning what was already walked.",
                        retryable=True,
                        retry_after=hint,
                        status=429,
                    )
                if throttled >= self.throttle_retries:
                    raise PageUnavailable(
                        f"GitLab throttled {where} (429) on {throttled + 1} "
                        f"attempts in a row, asking for {hint} ({source}). The "
                        f"walk stops with the pages it has; the hint travels "
                        f"with the failure.",
                        retryable=True,
                        retry_after=hint,
                        status=429,
                    )
                if self.wait is _does_not_wait:
                    raise PageUnavailable(
                        f"GitLab throttled {where} (429) and asked for {hint} "
                        f"({source}), and this client was composed with no way "
                        f"to wait. Re-asking now would be the retry GitLab just "
                        f"refused, so the hint travels with the failure.",
                        retryable=True,
                        retry_after=hint,
                        status=429,
                    )
                throttled += 1
                self.wait(hint.total_seconds())
                continue
            if status >= 500:
                raise PageUnavailable(
                    f"GitLab answered {status} for {where}. A server error says "
                    f"nothing about the commits, so nothing is claimed for the "
                    f"page that did not arrive.",
                    retryable=True,
                    status=status,
                )
            raise PageUnavailable(
                f"GitLab answered {status} for {where}, which is not a status "
                f"this client has a reading for. Refusing rather than guessing "
                f"what it meant.",
                retryable=False,
                status=status,
            )

    def _send(self, url: str, *, timeout: float = SOCKET_TIMEOUT_SECONDS) -> GitLabResponse:
        """One transport call with the headers every request carries.

        An exception from an injected transport that is not a `PageUnavailable`
        cannot be told apart from a bug in pm-ai's own wiring, so it takes
        `UNCLASSIFIED_FAULT_IS_RETRYABLE`'s verdict, named by type.
        """
        request = GitLabRequest(
            url=url,
            headers={
                TOKEN_HEADER: self.token,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=timeout,
        )
        try:
            answered = self.transport(request)
        except PageUnavailable:
            raise
        except Exception as unclassified:  # noqa: BLE001 — named by type only
            raise PageUnavailable(
                f"the request to {_path_only(url)} raised "
                f"{type(unclassified).__name__} out of the transport, which "
                f"pm-ai cannot classify.",
                retryable=UNCLASSIFIED_FAULT_IS_RETRYABLE,
            ) from unclassified
        if not isinstance(answered, GitLabResponse):
            raise PageUnavailable(
                f"the transport answered with {type(answered).__name__} rather "
                f"than a GitLabResponse — a wiring fault in pm-ai rather than "
                f"anything GitLab said.",
                retryable=False,
            )
        return answered

    def _retry_after(self, response: GitLabResponse) -> tuple[timedelta, str]:
        """The wait a 429 asked for, and a phrase saying where it came from.

        Seconds only. GitLab states its hint in seconds; an absent header, a
        date, or anything else waits the stated default.
        """
        raw = response.header(RETRY_AFTER_HEADER)
        stated = (raw or "").strip()
        if _DIGITS.fullmatch(stated):
            if len(stated) > MAX_HEADER_DIGITS:
                # More seconds than any budget holds; read as the longest
                # hint rather than handed to `int()`.
                return _LONGEST_HINT, f"a Retry-After of {len(stated)} digits"
            hint = min(timedelta(seconds=int(stated)), _LONGEST_HINT)
            return hint, f"its Retry-After of {stated} seconds"
        return self.default_backoff, (
            f"no Retry-After in seconds, so the stated {self.default_backoff} default"
        )
