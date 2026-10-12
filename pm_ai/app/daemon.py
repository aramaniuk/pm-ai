"""`pm-ai daemon run`: the long-lived daemon, in the foreground (AD-7, AD-8, story 4e).

The composition root owns the lifecycle (AD-30), so this is where the daemon
binds, takes its token, serves and stops. What it serves is built in
`pm_ai.surfaces.api.app`; what it serves *with* is the `Daemon` that
`pm_ai.app.entry` composed once for this process.

## The order, and why it is this one

1. **The runtime extra.** `fastapi` and `uvicorn` are an install extra, not a
   default. Without them the start is refused, naming the extra.
2. **The port.** `127.0.0.1` and nothing else, on `daemon_port`. A port already
   taken refuses the start naming the port and the key. Taken *before* the
   token is touched: that is what keeps the daemon single — a second
   `daemon run` meets the first one's port — and what keeps the loser of two
   simultaneous starts from writing anything.
3. **The token.** Read if present, minted if absent, with an exclusive create so
   a file that appeared meanwhile is read rather than replaced. A file that is
   unusable refuses the start naming it, and is never minted over.
4. **Serve**, until Ctrl-C, a stop signal, or the terminal closing (`SIGINT`,
   `SIGTERM`, `SIGHUP` — all three are a stop, none is re-raised afterwards).
5. **Stop**: stop listening, close the writer, return 0.

## One writer, one loop, one thread

The daemon composed once, before this module was reached, and nothing here
composes again: every request reads through the writer the daemon already
holds. The status endpoint runs on the event loop's own thread, which in this
slice is the only thread that touches the writer — `4r` brings the one executor
and the lock every worker takes. Background work (`9a`'s timers, `9b`'s render)
attaches in `_lifespan`, when the application starts serving, on this loop.

## The thin clients still write

In this wave `pm-ai dashboard`, `connector add`, `goal set` and the rest keep
writing directly, serialized by AD-5's one cross-process claim; this daemon is
one more writer under the same claim, until story 10 makes it the sole writer.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import secrets
import signal
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from datetime import datetime, timezone
from types import FrameType
from typing import Any

from pm_ai.app.wiring import Daemon
from pm_ai.domain.api_token import TOKEN_BYTES, ApiTokenRefused
from pm_ai.domain.health import Report
from pm_ai.ports import DaemonRefused
from pm_ai.surfaces.api.app import REQUEST_LOGGER, create_app

__all__ = ["LOOPBACK", "REQUEST_LOG", "bind_loopback", "serve", "status_body", "take_token"]

LOOPBACK = "127.0.0.1"
"""The one address the daemon listens on (AD-2). Never a wildcard, never another interface."""

REQUEST_LOG = "daemon.log"
"""The file under `~/.pm-ai/logs/` the request log rotates through (AD-24)."""

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
"""Ctrl-C, a stop signal, and the terminal closing — each a clean stop, exit 0."""

_PORT_KEY = "daemon_port"


def serve(
    daemon: Daemon,
    *,
    probes: Callable[[], Report],
    announce: Callable[[str], None] | None = None,
) -> int:
    """Run the daemon in the foreground until it is told to stop; return 0.

    `probes` is `run_all` over what the daemon holds, bound by the composition
    root — `GET /v1/status` reports it without a `daemon` probe, because this
    process is the daemon and never asks itself. `announce` is where the one
    line saying where it listens goes.

    Raises `DaemonRefused` for every reason it will not start, each naming its
    cause, before anything is served.
    """
    uvicorn = _require_runtime_extra()
    port = daemon.config.daemon_port
    listener = bind_loopback(port)
    try:
        token = take_token(daemon)
    except BaseException:
        listener.close()
        raise
    started_at = daemon.clock()
    started = time.monotonic()
    request_log: _RequestLog | None = None
    try:
        request_log = _RequestLog.attach(daemon)
        app = create_app(
            token=token,
            status=lambda: status_body(
                probes(), started_at=started_at, started=started
            ),
            lifespan=_lifespan,
        )
        server = _server_class(uvicorn)(
            uvicorn.Config(
                app,
                host=LOOPBACK,
                port=port,
                lifespan="on",
                # Configured by nobody here: uvicorn's own access log would
                # print the client's address and the query string, and the
                # request log above is the one that is allowed to exist.
                log_config=None,
                access_log=False,
                server_header=False,
            )
        )
        # Announced once the stop signals are handled and the application is
        # serving, never before: a supervisor that reads the line and sends a
        # stop must get a clean stop, not the default action of the signal.
        server.announce = lambda: (announce or _announce)(
            f"pm-ai daemon listening on {LOOPBACK}:{port} — in the foreground "
            f"until Ctrl-C or a stop signal."
        )
        try:
            asyncio.run(server.serve(sockets=[listener]))
        except SystemExit as stopped:
            # uvicorn's own `sys.exit` when its startup fails — a lifespan hook
            # that raised, a socket it could not serve on. Never an exit 0.
            raise DaemonRefused(
                f"the daemon bound {LOOPBACK}:{port} but failed to start serving "
                f"(the server stopped during startup with code {stopped.code}); "
                f"its error is printed above. Nothing was served."
            ) from stopped
        except OSError as unservable:
            # On the `sockets=` path uvicorn does not turn a `create_server`
            # failure into its startup exit; it arrives here as itself.
            raise DaemonRefused(
                f"the daemon bound {LOOPBACK}:{port} (`daemon_port`) but could "
                f"not serve on it ({unservable.strerror or unservable}). Nothing "
                f"was served."
            ) from unservable
        if not server.started:
            raise DaemonRefused(
                f"the daemon bound {LOOPBACK}:{port} but failed to start serving; "
                f"its error is printed above. Nothing was served."
            )
    finally:
        # Stop listening (uvicorn closed the socket when it shut down; closing
        # again is harmless), then close the writer. Nothing serves after this.
        listener.close()
        if request_log is not None:
            request_log.detach()
        daemon.storage.close()
    return 0


def _announce(line: str) -> None:
    # Flushed, so a terminal or a supervisor reading a pipe sees it at once.
    print(line, flush=True)


def _require_runtime_extra() -> Any:
    """`uvicorn`, after checking the whole web stack imports; or the refusal naming the extra."""
    try:
        import fastapi  # noqa: F401
        import uvicorn
    except ImportError as missing:
        raise DaemonRefused(
            f"`pm-ai daemon run` needs the `runtime` extra, and {missing.name or 'part of it'} "
            f"is not installed. Install it with `uv sync --extra runtime`; "
            f"nothing was started."
        ) from missing
    return uvicorn


def bind_loopback(port: int) -> socket.socket:
    """A listening socket on `127.0.0.1:port`, or `DaemonRefused` naming the port.

    Two checks, because one is not enough on macOS. The bind refuses a second
    listener on `127.0.0.1` itself — which is what keeps the daemon single. But
    `SO_REUSEADDR`, which a restart needs so that connections still in
    `TIME_WAIT` from the last run do not hold the port, also lets this socket
    bind *beside* a foreign service listening on every interface. So the port
    is asked first: anything that accepts a connection on it is something else
    already serving there, and the start is refused before binding.
    """
    if _answers(port):
        raise DaemonRefused(
            _taken(port, "a connection to it was accepted or did not finish, so something holds it")
        )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((LOOPBACK, port))
        listener.listen(128)
    except OSError as refused:
        listener.close()
        if refused.errno == errno.EADDRINUSE:
            raise DaemonRefused(_taken(port, "another process is bound to it")) from refused
        raise DaemonRefused(
            f"pm-ai could not listen on {LOOPBACK}:{port} ({refused}). The port "
            f"is `{_PORT_KEY}` in ~/.pm-ai/config.toml; nothing was started."
        ) from refused
    return listener


def _answers(port: int) -> bool:
    """Whether anything accepts a TCP connection on the loopback port now."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        result = probe.connect_ex((LOOPBACK, port))
    except TimeoutError:
        # Something is there and slow to accept: taken, not free.
        return True
    except OSError:
        return False
    finally:
        probe.close()
    # Refused is the one answer that means nothing listens. A connect still in
    # progress or timed out is something on the port that has not answered yet.
    return result != errno.ECONNREFUSED and (result == 0 or result in _UNFINISHED)


_UNFINISHED = frozenset(
    {errno.EAGAIN, errno.EWOULDBLOCK, errno.EINPROGRESS, errno.EALREADY, errno.ETIMEDOUT}
)


def _taken(port: int, why: str) -> str:
    return (
        f"port {port} on {LOOPBACK} is already taken — {why} — so the daemon "
        f"did not start. If it is another `pm-ai daemon run`, that one is the "
        f"daemon; `pm-ai doctor` says whether it answers. Otherwise choose a "
        f"free port with `{_PORT_KEY}` in ~/.pm-ai/config.toml."
    )


def take_token(daemon: Daemon) -> str:
    """The daemon's token: read if present, minted if absent, never minted over.

    The create is exclusive, so two daemons that both found no file — one of
    which will lose the port race and never get here, but a hand-run copy
    could — cannot replace each other's token: the second create fails, and
    that process reads what the first wrote.
    """
    storage = daemon.storage
    try:
        token = storage.read_api_token()
        if token is not None:
            return token
        minted = secrets.token_urlsafe(TOKEN_BYTES)
        try:
            storage.create_api_token(minted)
        except FileExistsError:
            found = storage.read_api_token()
            if found is None:
                raise DaemonRefused(
                    "the API token file appeared and vanished while the daemon "
                    "was minting one, so there is no token to check requests "
                    "against. Nothing was started; run it again."
                ) from None
            return found
        return minted
    except ApiTokenRefused as unusable:
        raise DaemonRefused(f"the daemon did not start: {unusable}") from unusable
    except OSError as unreadable:
        where = getattr(unreadable, "filename", None) or str(storage.api_token_path())
        raise DaemonRefused(
            f"the daemon did not start: the API token file {where} could not be "
            f"read or written ({unreadable.strerror or unreadable}). Nothing was "
            f"changed; check its ownership and permissions."
        ) from unreadable


def status_body(
    report: Report, *, started_at: datetime, started: float
) -> Mapping[str, object]:
    """`GET /v1/status`'s body: every probe, the verdict, and how long it has run.

    The fields `4r` and `4q` build against: `probes` (each a `name`, `state` and
    `detail`), `healthy`, `uptime_seconds` and `started_at`. `uptime_seconds` is
    read off the monotonic clock, so a wall-clock change does not make the
    daemon younger; `started_at` is the daemon's own clock at start, in UTC.
    """
    return {
        "probes": [
            {"name": probe.name, "state": probe.health.value, "detail": probe.detail}
            for probe in report.probes
        ],
        "healthy": report.healthy,
        "uptime_seconds": int(time.monotonic() - started),
        "started_at": started_at.astimezone(timezone.utc).isoformat(),
    }


@contextlib.asynccontextmanager
async def _lifespan(app: Any) -> AsyncIterator[None]:
    """Where background work attaches when the application starts serving.

    Empty in this slice. `9a`'s harvest timers and `9b`'s render start here, on
    the daemon's one event loop, and stop on the way out — after the listener
    has stopped and before the writer is closed.
    """
    yield


class _JsonLines(logging.Formatter):
    """AD-24's structured diagnostic line: one JSON object per record.

    `message` is the human-readable `GET /v1/status 401`; `method`, `path` and
    `status` are the same facts as fields. Nothing else from the request is in
    a record to be written — the API wrapper never passes a header or a query.
    """

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "logger": record.name,
            "level": record.levelname.lower(),
            "message": record.getMessage(),
        }
        for key in ("method", "path", "status"):
            if hasattr(record, key):
                line[key] = getattr(record, key)
        return json.dumps(line, ensure_ascii=False)


class _RequestLog:
    """The request log's handler on its logger, and how to put the logger back."""

    def __init__(self, handler: logging.Handler, *, propagate: bool, level: int) -> None:
        self._handler = handler
        self._propagate = propagate
        self._level = level

    @classmethod
    def attach(cls, daemon: Daemon) -> _RequestLog:
        """Open `~/.pm-ai/logs/daemon.log` and route the request log to it, or refuse."""
        try:
            handler = daemon.storage.diagnostic_log(REQUEST_LOG)
        except OSError as unopenable:
            where = getattr(unopenable, "filename", None) or f"~/.pm-ai/logs/{REQUEST_LOG}"
            raise DaemonRefused(
                f"the daemon did not start: its request log at {where} could not "
                f"be opened ({unopenable.strerror or unopenable}). Check that "
                f"~/.pm-ai/logs is a directory pm-ai can write to."
            ) from unopenable
        handler.setFormatter(_JsonLines())
        logger = logging.getLogger(REQUEST_LOGGER)
        attached = cls(handler, propagate=logger.propagate, level=logger.level)
        logger.setLevel(logging.INFO)
        # Not propagated: the request log belongs in its file, and a root
        # handler configured by anything else in the process must not copy it
        # to a console.
        logger.propagate = False
        logger.addHandler(handler)
        return attached

    def detach(self) -> None:
        """Remove and close the handler; the logger gets back what it had."""
        logger = logging.getLogger(REQUEST_LOGGER)
        logger.removeHandler(self._handler)
        logger.propagate = self._propagate
        logger.setLevel(self._level)
        self._handler.close()


def _server_class(uvicorn: Any) -> Any:
    """uvicorn's `Server`, with pm-ai's stop signals and no re-raise afterwards.

    uvicorn handles `SIGINT` and `SIGTERM` and, once it has shut down, raises
    the captured signal again — which would turn a clean Ctrl-C into a
    `KeyboardInterrupt` out of `main` and an exit code of 3. The daemon also
    treats `SIGHUP`, the terminal closing, as a stop. So the capture is
    replaced: all three set uvicorn's own exit flag (a second Ctrl-C forces it,
    as uvicorn does), the previous handlers come back afterwards, and nothing
    is re-raised.
    """

    class _Server(uvicorn.Server):  # type: ignore[misc, name-defined]
        announce: Callable[[], None] | None = None

        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:
            if threading.current_thread() is not threading.main_thread():
                yield
                return
            previous = {sig: signal.signal(sig, self._stop) for sig in STOP_SIGNALS}
            try:
                yield
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)

        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            server: Any = self
            if server.started and server.announce is not None:
                server.announce()

        def _stop(self, sig: int, frame: FrameType | None) -> None:
            server: Any = self
            if server.should_exit and sig == signal.SIGINT:
                server.force_exit = True
            else:
                server.should_exit = True

    return _Server

