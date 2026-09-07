"""Story 33b — `_urllib_transport`, the one Graph path no other test reached.

`tests/connectors/test_graph_calendar_fetch.py` injects a scripted transport
into every `GraphClient` it builds, which is what makes "no network in any
test" structural there. The cost was that the *production* transport — the
default `GraphClient.transport` a real harvest spends a token through — ran in
no test at all, and the whole client is built on the premise that it faithfully
turns four different HTTP answers into four different statuses. Hard-wiring its
`HTTPError` branch to `status=500` left the entire suite green, which means the
401-refresh, the 403-`ConsentChanged`, the `Retry-After` and the
`GraphRefused` classifications were assumptions rather than behaviour.

**No socket, and it is the fixture that makes that so.** `urllib.request`'s
`build_opener` *and* `urlopen` are both replaced in-process: whichever of the
two the transport reaches, the answer comes from a list written here, and an
answer the list does not hold is an `AssertionError` rather than a DNS lookup.
Patching both is deliberate — the opener is what this module builds now, and
serving `urlopen` too means a regression that reverted to it is caught by an
assertion about redirects rather than by a hostname that fails to resolve.
Nothing here sleeps.
"""

from __future__ import annotations

import email.message
import io
import urllib.error
import urllib.request
from dataclasses import fields
from typing import Any

import pytest

from pm_ai.connectors.graph.client import (
    GRAPH_BASE,
    GRAPH_HOST,
    GraphClient,
    GraphProtocolError,
    GraphRequest,
    GraphResponse,
    GraphUnavailable,
    _urllib_transport,
)

URL = f"{GRAPH_BASE}/me/calendarView?startDateTime=2026-09-07T12:00:00Z"
TOKEN = "a-bearer-token-that-must-not-travel"


def _request(url: str = URL) -> GraphRequest:
    """One outbound request, carrying the headers a real one carries."""
    return GraphRequest(
        url=url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
            "Prefer": 'outlook.timezone="UTC"',
        },
    )


def _headers(pairs: dict[str, str]) -> email.message.Message:
    """Headers in the shape `urllib` hands back: an `email.message.Message`.

    A dict would satisfy `.items()` and would not satisfy `HTTPError`, and the
    point of this file is that the transport is exercised against the standard
    library's own shapes rather than against something convenient.
    """
    message = email.message.Message()
    for name, value in pairs.items():
        message[name] = value
    return message


class Answer:
    """What `urllib` yields for a status it does not consider an error.

    A context manager with `status`, `read()`, `headers` and `url`, which is the
    whole of the interface `_urllib_transport` uses. `url` is the *landed* URL —
    `urllib` sets it to wherever the request ended up — so it is what the
    same-origin check reads.
    """

    def __init__(
        self,
        status: int = 200,
        body: bytes = b'{"value": []}',
        headers: dict[str, str] | None = None,
        url: str = URL,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = _headers(headers or {"Content-Type": "application/json"})
        self.url = url

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Answer:
        return self

    def __exit__(self, *_: Any) -> bool:
        return False


def _error(
    status: int, body: bytes = b"{}", headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    """The exception `urllib` raises for a status it *does* consider an error.

    Which is every 4xx and 5xx — and, behind an opener that declines to follow
    them, every 3xx too, because no handler answers the redirect and
    `HTTPDefaultErrorHandler` raises it.
    """
    return urllib.error.HTTPError(
        URL, status, f"status {status}", _headers(headers or {}), io.BytesIO(body)
    )


class Sockets:
    """Stands in for everything in `urllib.request` that could open one.

    Records which entry point the transport used and which handlers it asked
    `build_opener` for, because two of this file's assertions are about *how*
    the request was made rather than about the answer.
    """

    def __init__(self, *answers: Answer | Exception) -> None:
        self.answers: list[Answer | Exception] = list(answers)
        self.handlers: list[Any] = []
        self.opened: list[urllib.request.Request] = []
        self.built = 0
        self.urlopened = 0

    # ── The two entry points ─────────────────────────────────────────────────

    def build_opener(self, *handlers: Any) -> Sockets:
        self.built += 1
        self.handlers.extend(handlers)
        return self

    def urlopen(self, request: Any, timeout: float | None = None) -> Answer:
        self.urlopened += 1
        return self.open(request, timeout=timeout)

    # ── What an opener does ──────────────────────────────────────────────────

    def open(self, request: Any, timeout: float | None = None) -> Answer:
        self.opened.append(request)
        if not self.answers:
            raise AssertionError(
                f"the transport asked for something the fixture does not have "
                f"({request.full_url!r}). Every answer a test expects to be "
                f"needed is written down, so an extra request is a redirect "
                f"that was followed or a retry that should not have happened."
            )
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def urls(self) -> list[str]:
        return [request.full_url for request in self.opened]

    @property
    def redirect_handlers(self) -> list[urllib.request.HTTPRedirectHandler]:
        return [
            handler
            for handler in self.handlers
            if isinstance(handler, urllib.request.HTTPRedirectHandler)
        ]


def _no_sockets(monkeypatch: pytest.MonkeyPatch, *answers: Answer | Exception) -> Sockets:
    """Replace both ways out of `urllib.request` for the duration of one test."""
    sockets = Sockets(*answers)
    monkeypatch.setattr(urllib.request, "build_opener", sockets.build_opener)
    monkeypatch.setattr(urllib.request, "urlopen", sockets.urlopen)
    return sockets


# ── The transport under test is the one production uses ──────────────────────


def test_the_transport_exercised_here_is_the_clients_own_default():
    """Otherwise this file could pin a helper nothing ever calls.

    `GraphClient.transport` is defaulted rather than required, which is the
    whole reason the real path went unexercised: every test supplied its own.
    """
    (transport,) = [field for field in fields(GraphClient) if field.name == "transport"]
    assert transport.default is _urllib_transport


# ── Statuses ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [401, 403, 429, 400, 404, 500, 503])
def test_every_status_graph_answers_with_arrives_as_exactly_that_status(status, monkeypatch):
    """An HTTP error status is an answer, and the client's whole job is to tell
    401 from 403 from 429 from every other 4xx.

    Parametrized over the four the client branches on plus the two 5xx, because
    a transport that collapsed them — to 500, or to a raise — would leave
    `GraphClient.page`'s refresh, `ConsentChanged`, `GraphThrottled` and
    `GraphRefused` paths unreachable while every existing test, which injects
    its statuses directly, went on passing.
    """
    sockets = _no_sockets(monkeypatch, _error(status))
    answer = _urllib_transport(_request())

    assert isinstance(answer, GraphResponse)
    assert answer.status == status, (
        "the status the provider answered with is the only thing that tells the "
        "client which of its four refusals to raise"
    )
    assert sockets.urls == [URL], "exactly one request, to the URL it was given"


def test_a_throttled_answers_headers_and_body_both_survive_the_transport(monkeypatch):
    """`Retry-After` rides on a 429, and `_retry_after` reads it off the response.

    A transport that dropped the headers would turn every provider hint into
    the stated default backoff, silently.
    """
    _no_sockets(
        monkeypatch,
        _error(429, body=b'{"error": {"code": "TooManyRequests"}}', headers={"Retry-After": "90"}),
    )
    answer = _urllib_transport(_request())

    assert answer.status == 429
    assert answer.header("Retry-After") == "90"
    assert answer.body == {"error": {"code": "TooManyRequests"}}


def test_a_200_with_a_json_body_arrives_parsed(monkeypatch):
    """The ordinary case, which no test had run either.

    `GraphResponse.body` is already-decoded JSON by contract — `GraphClient.
    page` calls `body.get("@odata.nextLink")` on it — so a transport handing
    back bytes would fail on the first real page.
    """
    _no_sockets(
        monkeypatch,
        Answer(body=b'{"value": [{"id": "one"}], "@odata.nextLink": "' + URL.encode() + b'"}'),
    )
    answer = _urllib_transport(_request())

    assert answer.status == 200
    assert answer.body == {"value": [{"id": "one"}], "@odata.nextLink": URL}
    assert answer.header("content-type") == "application/json", "matched case-insensitively"


def test_a_body_that_is_not_json_is_none_rather_than_a_raise(monkeypatch):
    """A 500 carrying an HTML error page is a status, not a protocol violation."""
    _no_sockets(monkeypatch, _error(500, body=b"<html>we are sorry</html>"))
    answer = _urllib_transport(_request())

    assert (answer.status, answer.body) == (500, None)


# ── Redirects: the bearer token must not reach a second origin ───────────────


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_is_a_typed_refusal_and_never_a_silent_success(status, monkeypatch):
    """The refusal `urllib`'s default opener would have made unnecessary — by
    following the `Location` with the `Authorization` header attached.

    Two properties, and both matter. It is refused *as a redirect* rather than
    returned as a `GraphResponse` whose status happens to be 3xx: `GraphClient.
    page` has no 3xx branch, so a returned 302 falls through to `GraphRefused`
    — a "request pm-ai got wrong", which is not what happened and not what an
    operator should be told. And exactly one request is made, so nothing was
    followed.
    """
    sockets = _no_sockets(monkeypatch, _error(status, headers={"Location": "https://evil.invalid/v1.0/me"}))

    with pytest.raises(GraphProtocolError) as refused:
        _urllib_transport(_request())

    assert str(status) in str(refused.value)
    assert "redirect" in str(refused.value)
    assert TOKEN not in str(refused.value), "a refusal about a token is not a place to print one"
    assert sockets.urls == [URL], "the Location was never requested"
    assert refused.value.retryable is False, (
        "waiting does not make handing a bearer token to another origin safe"
    )


def test_the_opener_the_transport_builds_declines_every_redirect(monkeypatch):
    """Asserted on the handler, because the refusal has to happen *before* the
    socket to the second origin, not after.

    `HTTPRedirectHandler.redirect_request` returns a new `Request` carrying the
    original headers — `Authorization` among them. Returning `None` instead is
    `urllib`'s own way to decline, and it is the only place the decision can be
    made: `_assert_on_host` guards the URL this client asks for and every
    `@odata.nextLink` it is offered, and a `Location` is neither.

    The companion assertion is `urlopened == 0`. `urllib.request.urlopen` uses
    the *default* opener, which follows redirects, so a transport that reached
    for it would carry the token off-origin however carefully the rest of this
    module checked hosts.
    """
    sockets = _no_sockets(monkeypatch, Answer())
    _urllib_transport(_request())

    assert sockets.urlopened == 0, (
        "urlopen goes through urllib's default opener, which follows a redirect "
        "and copies the Authorization header onto its target"
    )
    assert sockets.built == 1
    handlers = sockets.redirect_handlers
    assert handlers, "no redirect handler was installed, so urllib's own is in force"
    for handler in handlers:
        assert (
            handler.redirect_request(
                urllib.request.Request(URL, headers={"Authorization": f"Bearer {TOKEN}"}),
                io.BytesIO(b""),
                302,
                "Found",
                _headers({"Location": "https://evil.invalid/v1.0/me"}),
                "https://evil.invalid/v1.0/me",
            )
            is None
        ), (
            "the handler answered the redirect with a request instead of "
            "declining it, which is urllib sending the bearer token to "
            "whatever the Location named"
        )


def test_an_answer_from_a_different_origin_is_refused_rather_than_read(monkeypatch):
    """Belt-and-braces behind the declining opener, and its own refusal.

    If any handler ever moves the request, the token has already been sent by
    the time the response arrives — so the answer is refused rather than parsed
    and reported as a page of the calendar.
    """
    _no_sockets(monkeypatch, Answer(url="https://graph.microsoft.com.evil.invalid/v1.0/me"))

    with pytest.raises(GraphProtocolError) as refused:
        _urllib_transport(_request())

    assert "different origin" in str(refused.value)
    assert GRAPH_HOST in str(refused.value)
    assert TOKEN not in str(refused.value)


def test_an_origin_refusal_is_not_reworded_as_no_answer(monkeypatch):
    """The refusal above is raised *inside* the `with`, so it passes through the
    broad `except Exception` that turns anything unclassified into
    `GraphUnavailable` — which is retryable, and would tell a scheduler to come
    back and be handed the same off-origin answer again.
    """
    _no_sockets(monkeypatch, Answer(url="https://evil.invalid/v1.0/me"))

    with pytest.raises(GraphProtocolError) as refused:
        _urllib_transport(_request())
    assert not isinstance(refused.value, GraphUnavailable)


# ── The two bounds that predate the redirect fix ─────────────────────────────


def test_a_url_that_is_not_https_never_reaches_a_socket(monkeypatch):
    """The last place the scheme can be seen before one is opened.

    `urllib` would happily open `file://`, and this transport is the default for
    whatever `GraphClient.base` was pointed at.
    """
    sockets = _no_sockets(monkeypatch, Answer())

    with pytest.raises(GraphProtocolError, match="https"):
        _urllib_transport(_request("http://graph.microsoft.com/v1.0/me"))
    assert sockets.opened == [], "the request was built and then sent anyway"


def test_a_request_that_gets_no_answer_at_all_is_graph_unavailable(monkeypatch):
    """A socket error says nothing about the window, which is not the same as
    the window being empty — the distinction `GraphUnavailable` carries.
    """
    _no_sockets(monkeypatch, OSError("connection reset by peer"))

    with pytest.raises(GraphUnavailable) as silent:
        _urllib_transport(_request())

    assert "OSError" in str(silent.value)
    assert silent.value.retryable is True
