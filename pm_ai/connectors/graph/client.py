"""The Graph HTTP client: paging, throttling, and one 401 retry (story 33b).

`33a` obtains a token. This is the first module that spends one. It is
deliberately thin — one `GET`, followed as far as the provider's own
`@odata.nextLink` chain goes — because everything difficult about reaching
`calendarView` is transport rather than modelling: the endpoint pages, it
throttles, an administrator can revoke consent between page two and page three,
and the link that says where the next page lives arrives *inside a response
body*.

## Read-only, class H egress (AD-1)

`GET` and nothing else. There is no method parameter, so a mutation is not a
flag away: adding one would be adding a method.

## What the response body is allowed to decide

`@odata.nextLink` is provider-controlled data, and following it unconditionally
is an unbounded loop over a value pm-ai does not own. Three bounds, and each one
is a rule rather than hardening:

- **origin** — the link's scheme must be `https` and its host must be the Graph
  host this client was pointed at. A link to another origin would send the
  bearer token there.
- **a page cap** — a provider that always answers with a `nextLink` would
  otherwise page forever.
- **a seen-link set** — a provider that answers with a link it already gave
  loops without ever exceeding the cap.

Any of the three fires as a refusal, not as a silent stop: stopping quietly
would report a complete harvest of a window that was never fully walked.

## Why nothing here sleeps for longer than the provider asked

A 429 carries `Retry-After`, and honouring it *within this call* is what "the
interval the provider asked for" means — the same thing `33a` lets MSAL's own
loop do. That is not AD-9's forbidden cadence: it owns no schedule, keeps no
state between calls, and does not touch a cursor. What AD-9 reserves for the
daemon is the *retry* decision, and that is why a hint this client will not wait
out is handed upward on the failure instead: `HarvestFailure.retry_after` exists
precisely so the scheduler decides when to come back.

The line between the two is the run's budget. A hint that fits is waited out and
the page is retried; a hint that does not — a `Retry-After` of an hour, or an
HTTP-date next week — returns what was already walked rather than blocking a
background fetch past the next harvest cycle.

## The one retry that is not a backoff

A 401 mid-walk is a credential that was live when the walk started and expired
during it. `33a`'s `access_token(force_refresh=True)` is asked once and the page
is retried once; a second 401 is `CredentialStale`. A **403** is deliberately
not on that path at all — an administrator revoking `Calendars.Read` is a
consent change, and sending it through the refresh path would report a perfectly
good credential as stale and send the PM to re-enrol against a permission
nobody has granted.

## The request this client sends, and what it deliberately omits

`Prefer: outlook.timezone="UTC"` on every request. It is a request rather than a
guarantee — the service honours it, the protocol does not — so
`calendar.py` converts defensively regardless. Sending it anyway is what keeps
the ordinary case cheap and the defensive path rare.

No `$top` and no `$select`. Slice 0 measured three sibling collections
disagreeing about whether `$top` is even accepted (`_bmad-output/
implementation-artifacts/slice-0-graph-spike-2026-09-06.md`), and its conclusion
was that a paging option is a per-endpoint fact rather than a Graph-wide
convention. What that spike *did* exercise on `calendarView` is a plain range
request, so that is what this client sends: page size stays the service's
default and the walk follows the links it actually returns.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote, urlsplit

from pm_ai.connectors.graph.auth import CredentialStale
from pm_ai.domain.harvest import UNCLASSIFIED_FAULT_IS_RETRYABLE

__all__ = [
    "ConsentChanged",
    "DEFAULT_THROTTLE_BACKOFF",
    "FETCH_BUDGET",
    "FetchBudgetSpent",
    "GRAPH_BASE",
    "GRAPH_HOST",
    "GraphCallFailed",
    "GraphClient",
    "GraphFaultUnclassified",
    "GraphProtocolError",
    "GraphRefused",
    "GraphRequest",
    "GraphResponse",
    "GraphThrottled",
    "GraphUnavailable",
    "GraphUninterpretedStatus",
    "PAGE_CAP",
    "PREFER_UTC",
    "THROTTLE_RETRIES",
    "TokenSource",
]


# ── Where Graph is, and the bounds this client holds itself to ───────────────

GRAPH_HOST = "graph.microsoft.com"
"""The one origin a `@odata.nextLink` may point at.

Named separately from `GRAPH_BASE` because it is what the *link check* compares
against, and deriving it from the base URL at each check would make a
misconfigured base widen the check it is supposed to be measured by.
"""

GRAPH_BASE = f"https://{GRAPH_HOST}/v1.0"
"""`v1.0`, not `beta`. Nothing this slice reads is beta-only, and a beta
endpoint may change shape without a version bump — which for a connector means
a mapping that breaks on a Tuesday.
"""

PREFER_UTC = 'outlook.timezone="UTC"'
"""The `Prefer` value that asks Outlook to answer in UTC.

Asserted by this slice's tests on the recorded *request*, not inferred from the
rows: every other criterion observes output, so a client that never sent this
would pass all of them.
"""

PAGE_CAP = 200
"""How many pages one walk may follow before it refuses.

A ceiling on a loop whose continuation condition is a value the response body
controls. The number is a choice rather than a measurement: at Graph's default
`calendarView` page size it is far more than any window this connector asks
for, and small enough that a provider looping cannot page until the budget runs
out.
"""

DEFAULT_THROTTLE_BACKOFF = timedelta(seconds=30)
"""What "honoured" means for a 429 that carried no `Retry-After`.

Undefined otherwise, which is the point of stating it: a throttled request with
no hint would otherwise be retried immediately, which is the behaviour that
turns one 429 into a hundred.
"""

THROTTLE_RETRIES = 2
"""How many times one page may be re-asked after a 429 before the walk stops.

Two, so a single throttle does not end a harvest and a provider that is
genuinely rate-limiting this machine is not argued with. Beyond it the walk
returns what it has, with the hint, and the daemon decides.
"""

FETCH_BUDGET = timedelta(minutes=10)
"""How long one whole fetch may take, wall-clock.

A stated choice, not a measured requirement — no NFR bounds a harvest fetch.
What it is chosen against is CAP-2's 240-minute cycle: a fetch that can outlive
its own cycle would overlap the next one, so the budget sits far below it. It
also bounds the paging loop in the dimension the page cap does not — a provider
answering slowly rather than endlessly.
"""


# ── Refusals ─────────────────────────────────────────────────────────────────


class GraphCallFailed(Exception):
    """A Graph request could not be completed, carrying what a caller must decide.

    The retry semantics ride on the exception rather than inside its message,
    exactly as `gitlab.PageUnavailable`'s do: the fetcher turns this into a
    `HarvestFailure`, and "wait 90 seconds" is a different instruction to an
    operator than "an administrator revoked the permission".

    Never escapes the fetcher. A raise cannot carry the coverage the pages
    already walked earned, which is why the outcome is a value.

    `reason` must carry no credential material and no provider response body.
    It becomes a `HarvestFailure`, which is persisted in Tier 2, never rebuilt,
    and read back into reports. So the sentence is composed here — from a status
    code and this client's own words — rather than passed through from whatever
    the provider wrote.
    """

    retryable = False
    """Whether waiting could plausibly change the answer. Overridden per subclass.

    A class attribute rather than a constructor default, so a new subclass has
    to state it: `HarvestFailure.retryable` has no default for the same reason.
    """

    def __init__(self, reason: str, *, retry_after: timedelta | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class GraphThrottled(GraphCallFailed):
    """429. The provider is rate-limiting, and may have said for how long."""

    retryable = True


class GraphUnavailable(GraphCallFailed):
    """A 5xx, or a request that never got an answer at all.

    Both are "nothing was learned", which is the reading `GraphUnreachable`
    takes one layer up: retryable, and no coverage claimed for the page that
    did not arrive.

    "Never got an answer" means a frame that *knows* that — `_urllib_transport`,
    which opened the socket. An exception out of an injected transport is
    `GraphFaultUnclassified` instead: from `_send` there is no telling a dropped
    connection from a bug in pm-ai's own callable, and only one of those clears
    by waiting.
    """

    retryable = True


class ConsentChanged(GraphCallFailed):
    """403. The permission this call needs is not granted any more.

    Deliberately **not** retryable and deliberately not routed through `33a`'s
    refresh: the credential is fine and the grant is not. Reported as a consent
    change so the remedy names the app registration rather than the sign-in.
    """


class GraphRefused(GraphCallFailed):
    """A 4xx that is neither 401, 403 nor 429 — a request pm-ai got wrong.

    Not retryable, because re-sending a request the provider rejected on its
    own terms produces the same rejection. A `400 Query option 'Top' is not
    allowed` is this class, and slice 0 hit exactly that three times.
    """


class GraphUninterpretedStatus(GraphCallFailed):
    """A status this client has no reading for: not 2xx, 401, 403, 429 or 5xx.

    A 304, or any 1xx or 3xx that reaches the status dispatch. Its own refusal
    because the alternative was worse than untidy: the final branch below used to
    catch everything left over and report it as "a request pm-ai got wrong",
    which tells an operator to go and fix a request that Graph never rejected.

    Not retryable. Waiting does not turn an answer this client cannot interpret
    into one it can — the remedy is a reading for that status, here.
    """


class GraphFaultUnclassified(GraphCallFailed):
    """Something raised out of the injected transport that this client cannot place.

    Distinct from `GraphUnavailable`, which is a *classified* fault: a 5xx, or —
    in `_urllib_transport`, which knows it opened a socket — a request that
    demonstrably got no answer. This class is what is left when an injected
    callable raises anything at all, and the client cannot tell a network fault
    from a bug in pm-ai's own wiring.

    Its verdict is `pm_ai.domain.harvest.UNCLASSIFIED_FAULT_IS_RETRYABLE`, cited
    rather than restated, because `gitlab.harvest` answers the same question for
    the same reason and a scheduler reading `HarvestFailure.retryable` cannot see
    which provider produced it.
    """

    retryable = UNCLASSIFIED_FAULT_IS_RETRYABLE


class GraphProtocolError(GraphCallFailed):
    """The answer, or the link to the next one, was not something to walk.

    A body that is not a JSON object, or a `@odata.nextLink` that points off-host,
    repeats, or arrives past the page cap. Not retryable: nothing about waiting
    makes a link to another origin safe to follow.
    """


class FetchBudgetSpent(GraphCallFailed):
    """The walk ran out of its wall-clock budget with pages still to follow.

    Retryable, and the distinction matters: the pages already walked keep their
    coverage and the next run resumes from the span this one did not finish,
    rather than the window being reported complete.
    """

    retryable = True


# ── The wire, as a value ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GraphRequest:
    """One outbound `GET`, recorded rather than performed.

    A value because a matrix row asserts on it: "the `Prefer` header is asserted
    on the recorded request, not inferred from the rows". A test's transport
    keeps the list; there is nothing to inspect if the request only ever exists
    as arguments to a socket write.

    `headers` carries an `Authorization` value, so this object is as sensitive
    as the token in it — `__repr__` is overridden for exactly that reason.
    """

    url: str
    headers: Mapping[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        """The URL and which headers were sent — never their values.

        `Authorization: Bearer …` in a pytest assertion diff, a log line or a
        traceback is a token on disk. The header *names* are what the assertions
        are about, so those stay.
        """
        return f"GraphRequest(url={self.url!r}, headers={sorted(self.headers)!r})"


@dataclass(frozen=True, slots=True)
class GraphResponse:
    """What came back: a status, a parsed body, and the headers a caller reads.

    `body` is already-decoded JSON rather than bytes, because every transport
    this client will ever have — `urllib`, a recorded fixture, a future
    `httpx` — can decode JSON, and leaving it to the client would make the
    fixture the odd one out.
    """

    status: int
    body: Mapping[str, Any] | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        """One header, matched case-insensitively.

        HTTP header names are case-insensitive and every source spells them
        differently — `urllib` normalises, a recorded fixture does not. A
        `Retry-After` missed because it arrived as `retry-after` is a throttle
        reported as having no hint.
        """
        folded = name.casefold()
        for key, value in self.headers.items():
            if key.casefold() == folded:
                return value
        return None


@runtime_checkable
class TokenSource(Protocol):
    """The half of `GraphAuthPort` this client needs, and no more.

    `33a`'s `GraphDeviceCodeAuth` satisfies it structurally, as does
    `pm_ai.ports.GraphAuthPort`. Narrowed deliberately: a fake standing in for
    auth in a paging test should not have to implement `sign_in` and
    `check_health` to prove that page three retries after a 401.
    """

    def access_token(self, *, force_refresh: bool = False) -> str: ...


def _no_redirect_opener() -> Any:
    """A `urllib` opener that refuses a redirect rather than following it.

    The default opener follows 301/302/307/308 by itself, and
    `HTTPRedirectHandler.redirect_request` carries the original request's
    headers — `Authorization` among them — onto the new URL. So a `Location`
    pointing at another origin, or down to `http`, would hand the bearer token
    over *before* any check in this module ran: `_assert_on_host` guards the URL
    this client asks for and every `@odata.nextLink` it is offered, and a
    redirect is neither of those. That is why the refusal lives in the opener
    rather than in a check on the response — a check would run after the token
    had already been sent.

    Refused rather than re-checked-and-followed. `calendarView` does not
    redirect, so a redirect here is a fact about the network rather than about
    the calendar, and "follow it while the host still matches" would be a second
    walk that neither the page cap nor the seen-link set bounds.

    Returning `None` from `redirect_request` is `urllib`'s own way to decline:
    no handler answers the 3xx, so `HTTPDefaultErrorHandler` raises it as an
    `HTTPError` carrying the redirect status, which the caller below turns into
    a refusal naming what it would have cost to follow.
    """
    import urllib.request

    class RefusesRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *asked: Any, **named: Any) -> None:
            return None

    return urllib.request.build_opener(RefusesRedirects())


def _same_origin(sent: str, landed: str) -> bool:
    """Whether a response came back from the origin the request was sent to.

    Compared against the *request* rather than against `GRAPH_HOST`, because
    `GraphClient.host` is configurable and this transport is the default for
    whatever it is pointed at. The property worth asserting is not "this is
    Graph" — the client already checked that — but "the credential did not
    travel anywhere else".
    """
    asked, answered = urlsplit(sent), urlsplit(landed)
    return (asked.scheme, (asked.hostname or "").casefold(), asked.port) == (
        answered.scheme,
        (answered.hostname or "").casefold(),
        answered.port,
    )


def _urllib_transport(request: GraphRequest) -> GraphResponse:
    """The real transport: one `GET` over stdlib HTTP, imported at call time.

    Stdlib rather than a declared HTTP client, for two reasons. It adds no
    dependency to a package whose runtime extras are pinned by hand and audited
    one at a time; and `.importlinter` confines `httpx`, `requests` and
    `aiohttp` to this layer and `pm_ai.skills`, so nothing is bought by
    reaching for one here.

    **Enforcement gap, stated rather than hidden.** That confinement contract
    names the three declared clients and `msal`, and cannot name this one:
    import-linter refuses `urllib.request` as a forbidden module ("subpackages
    of external packages are not valid"), and forbidding `urllib` outright would
    forbid `urllib.parse` — a URL parser — in three layers that may legitimately
    split a string. So from this slice onward a misplaced adapter could speak
    HTTP from `pm_ai.platform` over the standard library and pass the gate. The
    `core-is-io-free` contract does forbid `urllib` in `pm_ai.core`, which is
    the layer where it would matter most; the remaining hole is recorded in
    `_bmad-output/implementation-artifacts/deferred-work.md`.

    Imported inside the call for the reason `33a` imports `msal` inside the
    call: a module that imports its transport at module scope cannot be loaded
    by a test environment that deliberately lacks it, and a test that skips
    reads as coverage.

    An HTTP error status is an *answer*, not an exception, so `HTTPError` is
    caught and returned as the status it carries — the whole point of this
    client is that 401, 403 and 429 mean three different things. Anything else
    raised out of the opener is a request that never got an answer.

    A **redirect is not an answer either**, and it is not followed: see
    `_no_redirect_opener`. `urllib`'s default opener would follow it and copy
    the `Authorization` header to the new URL, which is how a bearer token
    leaves the origin every other check in this module is about.
    """
    import urllib.error
    import urllib.request

    outbound = urllib.request.Request(  # noqa: S310 — https-only, see below
        request.url, headers=dict(request.headers), method="GET"
    )
    if outbound.type != "https":
        # Checked here as well as on every `nextLink`, because this is the last
        # place the scheme can be seen before a socket is opened. `urllib`
        # would happily open `file://`.
        raise GraphProtocolError(
            f"refusing to send a Graph request over {outbound.type!r}: this "
            f"client speaks https to {GRAPH_HOST} and nothing else."
        )
    try:
        with _no_redirect_opener().open(outbound, timeout=30) as answer:  # noqa: S310
            landed = str(getattr(answer, "url", "") or request.url)
            if not _same_origin(request.url, landed):
                # Belt-and-braces behind the refusing opener: if any handler
                # ever moves the request, the answer is refused rather than
                # read, because by then the token has already been sent.
                raise GraphProtocolError(
                    f"the Graph request to {_path_only(request.url)} was "
                    f"answered from {_path_only(landed)} — a different origin "
                    f"from the one the bearer token was sent to. Refusing the "
                    f"answer rather than reading it."
                )
            return GraphResponse(
                status=int(answer.status),
                body=_decoded(answer.read()),
                headers={key: value for key, value in answer.headers.items()},
            )
    except urllib.error.HTTPError as answered:
        if 300 <= int(answered.code) < 400:
            raise GraphProtocolError(
                f"Graph answered {answered.code} for {_path_only(request.url)} "
                f"with a redirect, and this client does not follow one: "
                f"`urllib`'s redirect handler copies the `Authorization` header "
                f"onto whatever the `Location` names, so an off-origin — or "
                f"plain-http — redirect would send the bearer token there "
                f"before any origin check could run. Not retryable: waiting "
                f"does not make that safe."
            ) from answered
        # A status this client interprets, not a failure. The body is read
        # because a 429 sometimes carries its hint there as well as in a header.
        return GraphResponse(
            status=int(answered.code),
            body=_decoded(answered.read()),
            headers={key: value for key, value in answered.headers.items()},
        )
    except GraphCallFailed:
        # The two refusals above are already the sentence a caller needs, so
        # they must not be reworded as "no answer" by the branch below.
        raise
    except Exception as silent:  # noqa: BLE001 — see the docstring
        raise GraphUnavailable(
            # The exception *type* and nothing else. A `urllib` or SSL message
            # carries the URL it failed on, `$skiptoken` and all, and this
            # sentence becomes a `HarvestFailure.reason` in Tier 2, which is
            # never rebuilt and is read back into reports. The traceback keeps
            # the message for whoever is debugging; the durable row does not.
            f"the Graph request did not get an answer ({type(silent).__name__}). "
            f"Nothing was learned about the window, which is not the same as it "
            f"being empty."
        ) from silent


def _does_not_wait(seconds: float) -> None:
    """The default `GraphClient.wait`: it returns without waiting, by name.

    Named rather than a lambda for the reason `gitlab._stubbed_reach` is: a
    lambda cannot be compared by identity, and the 429 branch needs to recognise
    "nobody supplied a real sleep" so it can refuse rather than re-ask a
    rate-limited provider immediately. A default that silently spins is the shape
    that makes a forgotten injection a production defect nothing reports.
    """
    return None


def _path_only(url: str) -> str:
    """A URL as it may appear in a refusal: path only, never the query.

    Module-level because the transport composes refusals too, and a second
    spelling of this rule is how a `$skiptoken` eventually reaches a durable
    row. `GraphClient._named` is this function.
    """
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}{parts.path}"


def _decoded(raw: bytes) -> Mapping[str, Any] | None:
    """A JSON object, or `None` for a body that is not one.

    `None` rather than a raise: a 204 has no body and a 500 often carries an
    HTML error page, and neither is a protocol violation worth a different
    refusal than the status already is.
    """
    if not raw:
        return None
    try:
        document = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return document if isinstance(document, dict) else None


# ── The client ───────────────────────────────────────────────────────────────


@dataclass
class GraphClient:
    """One authenticated, read-only, page-following reach into Graph.

    Holds no cursor and no schedule. It is handed a URL, it walks the provider's
    own link chain from it, and every bound it applies is either stated as a
    constant above or injected here.
    """

    auth: TokenSource
    """Where a bearer token comes from. `33a`'s adapter, or a fake standing in.

    Required, with no default, for the reason `GraphDeviceCodeAuth.store` is:
    there is no safe default for a credential, and a client that silently
    fetched without one would be a client sending unauthenticated requests.
    """

    transport: Callable[[GraphRequest], GraphResponse] = _urllib_transport
    """The seam every test replaces, and the real thing by default.

    Defaulted rather than required, unlike `auth`: a client with no transport
    could not reach Graph at all, and the gitlab connector records what a
    stubbed default costs — a health probe that reports `OK` on a socket nobody
    opened. Tests inject a recorded-payload transport, which is also what makes
    "no network in any test" structural rather than a promise: the fixtures are
    the only thing that answers.
    """

    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    """The clock, injected (AD-30). Coverage windows and the budget are judged
    against it, and a guard that cannot be tested deterministically is a guard
    nobody can trust.
    """

    wait: Callable[[float], None] = _does_not_wait
    """How a `Retry-After` this client will honour is waited out.

    Defaults to `_does_not_wait`, deliberately, and the composition root supplies
    `time.sleep`. A blocking sleep as the default is the shape that makes a
    forgotten injection cost ten minutes inside a test suite rather than
    failing loudly — and every row of this slice's matrix that involves a
    throttle asserts on what was recorded here rather than on elapsed time.

    **The default is recognised rather than trusted.** Nothing in `pm_ai`
    constructs a `GraphClient` yet, so the first composition is `33c`'s; a
    forgotten `wait` there would have turned every honoured hint into an
    immediate retry against a provider that is rate-limiting, and no test would
    have observed it. So the 429 branch compares this against
    `_does_not_wait` by identity and refuses with the hint instead of spinning.
    """

    host: str = GRAPH_HOST
    base: str = GRAPH_BASE
    page_cap: int = PAGE_CAP
    throttle_retries: int = THROTTLE_RETRIES
    default_backoff: timedelta = DEFAULT_THROTTLE_BACKOFF
    budget: timedelta = FETCH_BUDGET

    # ── One page ─────────────────────────────────────────────────────────────

    def page(self, url: str, *, deadline: datetime | None = None) -> Mapping[str, Any]:
        """One `GET`, with the 401 retry and the throttle handling around it.

        Returns the parsed body. Raises a `GraphCallFailed` subclass for every
        other outcome, so a caller can tell "wait" from "an administrator
        revoked this" from "pm-ai sent a request Graph rejected".
        """
        limit = deadline if deadline is not None else self.now() + self.budget
        token = self.auth.access_token()
        refreshed = False
        throttled = 0

        while True:
            self._assert_time_remains(url, limit)
            response = self._send(url, token)

            if 200 <= response.status < 300:
                body = response.body
                if body is None:
                    raise GraphProtocolError(
                        f"Graph answered {response.status} for "
                        f"{self._named(url)} with no JSON object to read. "
                        f"Refusing rather than reporting an empty window: a "
                        f"body pm-ai cannot parse is not evidence that there "
                        f"was nothing in the range."
                    )
                return body

            if response.status == 401:
                if refreshed:
                    # `33a`'s own type, so a caller has one thing to catch for
                    # "the credential is the problem" wherever it was noticed.
                    raise CredentialStale(
                        f"Graph refused {self._named(url)} with 401 twice — once "
                        f"on the token the walk started with, and again on a "
                        f"freshly refreshed one. Sign in again; this is the "
                        f"credential rather than the network."
                    )
                refreshed = True
                token = self.auth.access_token(force_refresh=True)
                continue

            if response.status == 403:
                raise ConsentChanged(
                    f"Graph refused {self._named(url)} with 403. The credential "
                    f"is valid and the permission is not granted — an "
                    f"administrator revoked it, or it was never consented to. "
                    f"Grant it on the app registration; signing in again will "
                    f"not change the answer, and this is deliberately not "
                    f"treated as a stale token."
                )

            if response.status == 429:
                hint = self._retry_after(response)
                stated = response.header("Retry-After")
                source = (
                    f"its Retry-After of {stated!r}"
                    if stated is not None and stated.strip()
                    else f"no Retry-After header, so the stated {self.default_backoff} default"
                )
                remaining = limit - self.now()
                if hint > remaining:
                    raise GraphThrottled(
                        f"Graph throttled {self._named(url)} and asked for "
                        f"{hint} ({source}), which is more than the "
                        f"{remaining} left of this run. Returning what was "
                        f"already walked rather than blocking a background "
                        f"fetch past its own budget.",
                        retry_after=hint,
                    )
                if throttled >= self.throttle_retries:
                    raise GraphThrottled(
                        f"Graph throttled {self._named(url)} on every one of "
                        f"{throttled + 1} attempts, asking for {hint} "
                        f"({source}). The walk stops here with the pages it "
                        f"already has; when to come back is the daemon's "
                        f"decision, and the hint travels with the failure.",
                        retry_after=hint,
                    )
                if self.wait is _does_not_wait:
                    # Loud rather than silent, exactly as `gitlab.check_health`
                    # compares `self.reach is _stubbed_reach`. The default
                    # `wait` does not wait, so honouring a hint through it would
                    # re-ask a rate-limited provider immediately — the behaviour
                    # that turns one 429 into a hundred — and nothing would say
                    # so. A composition that has not supplied a real sleep is
                    # handed the hint on a retryable failure instead.
                    raise GraphThrottled(
                        f"Graph throttled {self._named(url)} and asked for "
                        f"{hint} ({source}), and this client was composed with "
                        f"no way to wait: `GraphClient.wait` is still the "
                        f"default that returns immediately. Re-asking now would "
                        f"be a retry the provider just refused, so the hint "
                        f"travels with the failure and the daemon decides. "
                        f"Inject `time.sleep` at the composition root to honour "
                        f"a hint inside one call.",
                        retry_after=hint,
                    )
                throttled += 1
                self.wait(hint.total_seconds())
                continue

            if response.status >= 500:
                raise GraphUnavailable(
                    f"Graph answered {response.status} for {self._named(url)}. "
                    f"A provider error says nothing about the window, so no "
                    f"coverage is claimed for the page that did not arrive."
                )

            if response.status < 400:
                # A 304, or any 1xx/3xx that reaches this dispatch — through an
                # injected transport, or a status `_urllib_transport` did not
                # already refuse. Named rather than swept into the branch below:
                # reporting it as "a request pm-ai got wrong" sends an operator
                # to fix a request Graph never rejected.
                raise GraphUninterpretedStatus(
                    f"Graph answered {response.status} for {self._named(url)}, "
                    f"which is not a status this client interprets: it reads "
                    f"2xx, 401, 403, 429 and 5xx, and everything else as a 4xx. "
                    f"Refusing rather than guessing which of those it meant."
                )

            raise GraphRefused(
                f"Graph rejected {self._named(url)} with {response.status}. "
                f"That is a request pm-ai got wrong rather than a provider "
                f"fault, so re-sending it would be rejected identically."
            )

    # ── Every page ───────────────────────────────────────────────────────────

    def walk(self, url: str, *, deadline: datetime | None = None) -> Iterator[Mapping[str, Any]]:
        """Yield every page from `url`, following `@odata.nextLink` under bounds.

        A generator on purpose. The caller keeps the rows from every page it
        already consumed when a later one refuses — which is the whole of the
        matrix's "pages already walked returned with their real coverage" — and
        that is not expressible by a method that returns a list.

        The link is checked before it is followed, never after: origin, then the
        seen-link set, then the page cap.
        """
        limit = deadline if deadline is not None else self.now() + self.budget
        seen = {url}
        walked = 0
        following: str | None = url

        while following is not None:
            body = self.page(following, deadline=limit)
            walked += 1
            yield body
            link = body.get("@odata.nextLink")
            if link is None:
                return
            if not isinstance(link, str) or not link.strip():
                raise GraphProtocolError(
                    f"the page from {self._named(following)} carried an "
                    f"@odata.nextLink that is not a URL "
                    f"({type(link).__name__}). Refusing: a walk that cannot "
                    f"tell where the next page is must not report the window "
                    f"as fully walked."
                )
            self._assert_on_host(link)
            if link in seen:
                raise GraphProtocolError(
                    f"Graph handed back a page link it had already given while "
                    f"walking {self._named(following)}, after {walked} page(s). "
                    f"Following it again would never end, so the walk stops "
                    f"here and the pages already walked keep their coverage."
                )
            if walked >= self.page_cap:
                raise GraphProtocolError(
                    f"the walk from {self._named(url)} reached the {self.page_cap}-page "
                    f"cap with a further page still offered. The continuation "
                    f"condition is a value the response body controls, so it is "
                    f"bounded; refusing rather than reporting a complete "
                    f"harvest of a window that was not fully walked."
                )
            seen.add(link)
            following = link

    # ── URLs ─────────────────────────────────────────────────────────────────

    def calendar_view(self, start: datetime, end: datetime) -> str:
        """The `calendarView` URL for one span, built here so nothing derives a second.

        The two range parameters and nothing else — see the module docstring on
        why `$top` and `$select` are deliberately absent.

        The instants are sent with a trailing `Z` rather than as
        `isoformat()`'s `+00:00`. Graph reads either as absolute — which removes
        the one ambiguity the request itself could introduce, since a naive
        `startDateTime` is interpreted in the mailbox's timezone — but a literal
        `+` in a query string is a space, so the offset spelling has to be
        percent-encoded to survive and `Z` simply does not have the problem.

        Refuses a bound that is not aware UTC. `CalendarWindow` guarantees it,
        and this is the place where a caller bypassing that type would otherwise
        send a naive instant and get a window shifted by the mailbox's offset.
        """
        stamps = []
        for label, value in (("start", start), ("end", end)):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise GraphProtocolError(
                    f"refusing to request a calendarView whose {label} "
                    f"({value!r}) is not aware UTC: Graph would read it in the "
                    f"mailbox's own timezone and answer for a different range "
                    f"than the one pm-ai will report."
                )
            stamps.append(value.strftime("%Y-%m-%dT%H:%M:%SZ"))
        query = "&".join(
            f"{name}={quote(value, safe=':')}"
            for name, value in (("startDateTime", stamps[0]), ("endDateTime", stamps[1]))
        )
        return f"{self.base}/me/calendarView?{query}"

    # ── The parts every branch above leans on ────────────────────────────────

    def _send(self, url: str, token: str) -> GraphResponse:
        """One transport call, with the headers every Graph request carries.

        A transport that raises anything other than a `GraphCallFailed` is a
        request that never got an answer: broad on purpose, for the reason
        `33a`'s `_guarded` is broad — the transport is injected, so this module
        cannot name the exception types its HTTP client raises.
        """
        self._assert_on_host(url)
        request = GraphRequest(
            url=url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                # A request, not a guarantee. `calendar.py` converts whatever
                # comes back regardless — see this slice's Design Notes.
                "Prefer": PREFER_UTC,
            },
        )
        try:
            answered = self.transport(request)
        except GraphCallFailed:
            raise
        except Exception as unclassified:  # noqa: BLE001 — see the docstring
            raise GraphFaultUnclassified(
                # The type, never the message: an exception a transport raises
                # can carry the whole URL — `$skiptoken` included — and this
                # sentence is persisted in Tier 2 and read back into reports.
                #
                # `GraphFaultUnclassified` rather than `GraphUnavailable`,
                # because this frame cannot tell a socket that dropped from a
                # bug in an injected callable, and only the first of those
                # clears by waiting. The shared verdict is stated once, in
                # `UNCLASSIFIED_FAULT_IS_RETRYABLE`.
                f"the request to {self._named(url)} raised "
                f"{type(unclassified).__name__} out of the transport, which is "
                f"not an answer this client can classify."
            ) from unclassified
        if not isinstance(answered, GraphResponse):
            raise GraphProtocolError(
                f"the injected transport answered with "
                f"{type(answered).__name__} rather than a GraphResponse, so "
                f"there is no status to interpret. That is a wiring fault in "
                f"pm-ai rather than anything Graph said."
            )
        return answered

    def _assert_on_host(self, url: str) -> None:
        """The one origin check, applied to the first URL and to every link.

        Applied to the *first* URL too, and not only to `nextLink`s: the base is
        configurable, and a check that trusts its own starting point cannot
        catch a misconfigured one.
        """
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").casefold() != self.host.casefold():
            raise GraphProtocolError(
                f"refusing a Graph page link that points at "
                f"{parts.scheme}://{parts.hostname} rather than https://"
                f"{self.host}. The link arrives inside a response body, so its "
                f"origin is checked before it is followed — a bearer token must "
                f"not be sent anywhere else."
            )

    def _assert_time_remains(self, url: str, deadline: datetime) -> None:
        """Refuse before opening a socket the run has no time left for."""
        if self.now() >= deadline:
            raise FetchBudgetSpent(
                f"the fetch spent its {self.budget} budget before "
                f"{self._named(url)} was asked. What was already walked is "
                f"returned with its real coverage; the rest of the window is "
                f"the next run's."
            )

    def _retry_after(self, response: GraphResponse) -> timedelta:
        """The provider's hint, in either spelling, or the stated default.

        `Retry-After` is defined as *either* a number of seconds or an
        HTTP-date, and Graph sends both. A parser that handled only the first
        would read a date as "no hint" and fall back to the default — a
        throttled connector retrying in 30 seconds against a provider that said
        next Tuesday.

        A hint in the past — a date already gone, or a negative number — is
        zero rather than negative: it means "now", and a negative `timedelta`
        would compare as fitting any budget while reading as nonsense in the
        refusal.

        A hint no interval can hold is bounded rather than allowed to raise.
        `float("inf")`, `1e400` — which *is* `inf` — and `nan` are not durations,
        so they are read as no hint at all and take the stated default; and a
        finite but absurd number of seconds (`1e30`) is clamped to this run's
        budget, which is the longest delay this call could ever have honoured.
        Both used to raise `OverflowError` out of the 429 branch, where the
        fetcher caught it as a fault nobody classified — losing the throttle, the
        hint and the retryable verdict to a header value.
        """
        raw = response.header("Retry-After")
        if raw is None or not raw.strip():
            return self.default_backoff
        stated = raw.strip()
        try:
            seconds = float(stated)
        except ValueError:
            seconds = None
        if seconds is not None:
            if not math.isfinite(seconds):
                # Not a duration. Read as "the provider gave no usable hint"
                # rather than as an unbounded one.
                return self.default_backoff
            try:
                return timedelta(seconds=max(0.0, seconds))
            except (OverflowError, OSError, ValueError):
                return max(timedelta(0), self.budget)
        try:
            at = parsedate_to_datetime(stated)
        except (TypeError, ValueError, OverflowError):
            # `OverflowError` for a date past `datetime`'s range, alongside the
            # two the parser documents: a header value must not be able to raise
            # out of the throttle branch at all.
            return self.default_backoff
        if at.tzinfo is None:
            # RFC 7231 dates are GMT; a parser that returns naive has told us
            # the zone by omission rather than by absence.
            at = at.replace(tzinfo=timezone.utc)
        return max(timedelta(0), at - self.now())

    def _named(self, url: str) -> str:
        """A URL as it may appear in a refusal: path only, never the query.

        A `nextLink`'s `$skiptoken` is opaque provider state, and it lands in
        `HarvestFailure.reason`, which is written to Tier 2 and read back into
        reports. `Cursor.__repr__` hides the same class of value for the same
        reason.
        """
        return _path_only(url)
