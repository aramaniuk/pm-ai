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
import inspect
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AuthenticatedApp",
    "FRONTIER_KEY_PATH",
    "IDENTITY_HEADER",
    "JOB_EVENTS_PATH",
    "JOB_PATH",
    "LINE_LIMIT",
    "PROMPT_PATH",
    "REQUEST_LOGGER",
    "Reply",
    "STATUS_PATH",
    "TOKEN_REQUIRED",
    "create_app",
]

STATUS_PATH = "/v1/status"

PROMPT_PATH = "/v1/prompt"
"""Takes `{"line": "..."}`; answers inline (`200`) or with a job id (`202`) (story 4r)."""

JOB_PATH = "/v1/jobs/{job_id}"
"""One job's row; `404` for an id the daemon does not hold."""

JOB_EVENTS_PATH = "/v1/jobs/{job_id}/events"
"""One job's state changes, as an event stream that closes after the last."""

FRONTIER_KEY_PATH = "/v1/frontier-key"
"""Takes `{"key": "..."}` from `pm-ai frontier key`; answers inline, as a line is (story 7c).

The one secret the API carries, over the authenticated loopback channel (AD-8):
the daemon checks it with Anthropic and seals it. The request log records the
method, path and status only, never a body."""

KEY_LIMIT = 4096
"""A key of this many characters or more is refused `400`."""

LINE_LIMIT = 4096
"""A line of this many characters or more is refused `400`, naming `line`."""

EVENT_STREAM = "text/event-stream"


@dataclass(frozen=True, slots=True)
class Reply:
    """What the daemon answers one request with: a status, a JSON body, headers."""

    status: int
    body: Mapping[str, object]
    headers: Mapping[str, str] = field(default_factory=dict)


Prompt = Callable[[str], Awaitable[Reply]]
Job = Callable[[str], Awaitable[Mapping[str, object] | None]]
JobEvents = Callable[[str], Awaitable[AsyncIterator[Mapping[str, object]] | None]]

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

Status = Callable[[], Mapping[str, object] | Awaitable[Mapping[str, object]]]
Lifespan = Callable[[Any], AbstractAsyncContextManager[None]]

_log = logging.getLogger(REQUEST_LOGGER)


def create_app(
    *,
    token: str | None,
    status: Status | None = None,
    lifespan: Lifespan | None = None,
    prompt: Prompt | None = None,
    job: Job | None = None,
    job_events: JobEvents | None = None,
    frontier_key: Prompt | None = None,
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
    from fastapi.responses import JSONResponse, StreamingResponse
    from pydantic import BaseModel, ConfigDict, StrictStr

    class PromptBody(BaseModel):
        """`{"line": "..."}`; anything else is the framework's `422`."""

        model_config = ConfigDict(extra="ignore")
        line: StrictStr

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
        # Async, and the probes are not run here: since story 4r the daemon's
        # `status` hands them to its I/O pool and this awaits the answer, so
        # the keychain fetch and the `git` subprocess never block the loop
        # (AD-19). A plain value is still accepted, for a caller with nothing
        # slow to report.
        if status is None:
            return JSONResponse({"detail": "no status source was composed"}, status_code=503)
        body = status()
        if inspect.isawaitable(body):
            body = await body
        return JSONResponse(dict(body))

    async def post_prompt(body: PromptBody) -> JSONResponse:
        line = body.line
        if not line or len(line) >= LINE_LIMIT:
            return JSONResponse(
                {
                    "detail": (
                        f"`line` must be between 1 and {LINE_LIMIT - 1} characters; "
                        f"this one has {len(line)}."
                    )
                },
                status_code=400,
            )
        if prompt is None:
            return JSONResponse({"detail": "no prompt handler was composed"}, status_code=503)
        reply = await prompt(line)
        return JSONResponse(dict(reply.body), status_code=reply.status, headers=dict(reply.headers))

    # The model is local to this function (the framework is imported here, so
    # importing the module never needs the runtime extra), and annotations are
    # strings under `from __future__ import annotations` — so the framework is
    # handed the class itself rather than a name it cannot resolve.
    post_prompt.__annotations__["body"] = PromptBody
    api.post(PROMPT_PATH)(post_prompt)

    class KeyBody(BaseModel):
        """`{"key": "..."}`; anything else is the framework's `422`."""

        model_config = ConfigDict(extra="ignore")
        key: StrictStr

    async def post_frontier_key(body: KeyBody) -> JSONResponse:
        # Story 7c: never logged, never echoed — the answer names no part of it.
        if not body.key or len(body.key) >= KEY_LIMIT:
            return JSONResponse(
                {"detail": f"`key` must be between 1 and {KEY_LIMIT - 1} characters."},
                status_code=400,
            )
        if frontier_key is None:
            return JSONResponse({"detail": "no key handler was composed"}, status_code=503)
        reply = await frontier_key(body.key)
        return JSONResponse(dict(reply.body), status_code=reply.status, headers=dict(reply.headers))

    post_frontier_key.__annotations__["body"] = KeyBody
    api.post(FRONTIER_KEY_PATH)(post_frontier_key)

    @api.get(JOB_PATH)
    async def get_job(job_id: str) -> JSONResponse:
        row = None if job is None else await job(job_id)
        if row is None:
            return JSONResponse({"detail": f"no job {job_id!r} is known"}, status_code=404)
        return JSONResponse(dict(row))

    @api.get(JOB_EVENTS_PATH)
    async def get_job_events(job_id: str) -> Any:
        events = None if job_events is None else await job_events(job_id)
        if events is None:
            return JSONResponse({"detail": f"no job {job_id!r} is known"}, status_code=404)

        async def frames() -> AsyncIterator[bytes]:
            try:
                async for event in events:
                    yield f"data: {json.dumps(dict(event), ensure_ascii=False)}\n\n".encode(
                        "utf-8"
                    )
            finally:
                # A reader that left mid-stream: the subscription is let go.
                close = getattr(events, "aclose", None)
                if close is not None:
                    await close()

        # The framework's own kept-open response with the event-stream type —
        # no package beyond it (story 4r). A reader that leaves closes it; the
        # job keeps running and its result stays in the row.
        return StreamingResponse(
            frames(),
            media_type=EVENT_STREAM,
            headers={"Cache-Control": "no-store"},
        )

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
