"""The daemon's loopback HTTP API (AD-8, story 4e).

One transport for every client that is not the daemon: HTTP on `127.0.0.1`,
authenticated by the per-user token in `~/.pm-ai/private/api_token`. This module
builds the application and nothing else — it binds no socket, reads no file and
composes nothing. `pm_ai.app.daemon` owns the lifecycle (AD-30): it binds, reads
or mints the token, and hands this module the token and the status callable.

## Every request carries the token

`Authorization: Bearer <token>`, compared with `hmac.compare_digest` over two
fixed-length digests, so the comparison takes the same time however much of the
value matches and whatever its length. A request without the header, or with
any other value, is answered `401` with a body saying only that a valid token
is required — the same answer for an unknown path, so an unauthenticated caller
learns nothing about what exists. The token never appears in a response, a log
line, or a web address.

## The request log

Method, path and status, one line per request, to the logger named
`REQUEST_LOGGER`; the daemon attaches the rotating diagnostic handler under
`~/.pm-ai/logs/` (AD-24). The path is the ASGI `path`, which never carries the
part of the address after `?`, and no header is ever logged.

## The lazily-served `app`

`test_ad8_loopback_api_rejects_unauthenticated_requests` imports this module and
serves `app` with nothing composed. So the module-level `app` is built on first
access (module `__getattr__`) with **no token**, which answers every request
`401`; `fastapi` is imported inside `create_app`, as `msal` is in the Graph
adapter, so importing this module never needs the runtime extra.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

__all__ = [
    "AuthenticatedApp",
    "IDENTITY_HEADER",
    "REQUEST_LOGGER",
    "STATUS_PATH",
    "TOKEN_REQUIRED",
    "create_app",
]

STATUS_PATH = "/v1/status"

REQUEST_LOGGER = "pm_ai.api.requests"
"""The logger the request log goes to. The daemon attaches the file handler."""

TOKEN_REQUIRED = "a valid token is required"
"""The whole of a `401` body's `detail`. Says nothing about what was sent."""

IDENTITY_HEADER = "x-pm-ai"
"""Present on every response this application sends, `401`s included.

How `pm-ai doctor` tells a pm-ai daemon from something else answering on the
port: a foreign service would make `pm-ai daemon run` refuse, and the operator
needs to know which of the two they are looking at.
"""

_IDENTITY = (IDENTITY_HEADER.encode("latin-1"), b"1")

POLICY_VIOLATION = 1008
"""The close code an unauthenticated websocket is refused with."""

SERVER_ERROR = 500

LOGGED_PATH_LIMIT = 200
"""The most characters of a path the request log keeps; the rest is cut and marked."""


def _loggable(path: str) -> str:
    if len(path) <= LOGGED_PATH_LIMIT:
        return path
    return path[:LOGGED_PATH_LIMIT] + "…"

Status = Callable[[], Mapping[str, object]]
Lifespan = Callable[[Any], AbstractAsyncContextManager[None]]

_log = logging.getLogger(REQUEST_LOGGER)


def create_app(
    *,
    token: str | None,
    status: Status | None = None,
    lifespan: Lifespan | None = None,
) -> AuthenticatedApp:
    """The application: one bearer check, one endpoint, one request log.

    `token` is the daemon's token; `None` refuses every request, which is what
    the lazily-served `app` is. `status` returns the body of `GET /v1/status`
    and is called once per request — the daemon composes once at start and
    never per request, so what it reads is what the daemon already holds.
    `lifespan` is where background work attaches when the application starts
    serving (`9a`'s timers, `9b`'s render), on the daemon's one event loop.
    """
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    api = FastAPI(
        title="pm-ai",
        lifespan=lifespan,
        # No generated documentation routes: every path but the one below is a
        # 404, and an unauthenticated caller is answered before routing anyway.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @api.get(STATUS_PATH)
    async def read_status() -> JSONResponse:
        # Async on purpose: the probes run on the daemon's one loop and its
        # one thread, which is the only thread that touches the writer in this
        # slice. A plain `def` would be dispatched to a worker thread.
        if status is None:
            return JSONResponse({"detail": "no status source was composed"}, status_code=503)
        return JSONResponse(dict(status()))

    return AuthenticatedApp(api, token)


class AuthenticatedApp:
    """The bearer check and the request log, around the whole application.

    A plain ASGI wrapper rather than a Starlette middleware class: it sees every
    request before routing, so an unknown path without a token is a `401` and
    not a `404`, and it never buffers a response body, so a streamed answer
    (`4r`) passes through it unchanged. Lifespan messages pass straight through.

    Attribute access falls through to the wrapped application, so a caller can
    still reach `routes` or `state` on what `create_app` returned.
    """

    def __init__(self, app: Any, token: str | None) -> None:
        self._app = app
        self._expected = None if token is None else _digest(token.encode("utf-8"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._app, name)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            # The one scope that carries no request: the server telling the
            # application it is starting or stopping.
            await self._app(scope, receive, send)
            return
        if kind != "http":
            # A websocket — or any scope type a later server invents — is held
            # to the same token. No route serves one today; refused unless
            # authorized, so nothing reaches the application unauthenticated.
            if self._authorized(scope):
                await self._app(scope, receive, send)
                return
            if kind == "websocket":
                await receive()  # the `websocket.connect` the server sends first
                await send({"type": "websocket.close", "code": POLICY_VIOLATION})
            return
        method = scope.get("method", "?")
        # `path` is the decoded path alone; the query string travels separately
        # in `query_string` and is never read here. Cut to a fixed length, so a
        # junk path cannot flood the log.
        path = _loggable(scope.get("path", "?"))
        answered: list[int] = []

        async def respond(message: Mapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                answered.append(int(message["status"]))
                message = {
                    **message,
                    "headers": [*message.get("headers", ()), _IDENTITY],
                }
            await send(message)

        failed = False
        try:
            if self._authorized(scope):
                await self._app(scope, receive, respond)
            else:
                await _refuse(respond)
        except BaseException:
            failed = True
            raise
        finally:
            # A handler that raised before answering is answered 500 by the
            # server, and the log says so rather than leaving the status blank.
            status: int | None = (
                answered[0] if answered else (SERVER_ERROR if failed else None)
            )
            _log.info(
                "%s %s %s",
                method,
                path,
                "-" if status is None else status,
                extra={"method": method, "path": path, "status": status},
            )

    def _authorized(self, scope: Mapping[str, Any]) -> bool:
        if self._expected is None:
            return False
        supplied = b""
        for name, value in scope.get("headers", ()):
            if name.lower() == b"authorization":
                supplied = value
                break
        scheme, _, credential = supplied.partition(b" ")
        if scheme.lower() != b"bearer" or not credential:
            return False
        return hmac.compare_digest(_digest(credential), self._expected)


def _digest(value: bytes) -> bytes:
    """A fixed-length stand-in, so `compare_digest` never sees two lengths."""
    return hashlib.sha256(value).digest()


async def _refuse(send: Callable[[Mapping[str, Any]], Any]) -> None:
    body = b'{"detail":"' + TOKEN_REQUIRED.encode("ascii") + b'"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def __getattr__(name: str) -> Any:
    """Serve `app` lazily: built on first access, with no token (refuses all)."""
    if name == "app":
        built = create_app(token=None)
        globals()["app"] = built
        return built
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
