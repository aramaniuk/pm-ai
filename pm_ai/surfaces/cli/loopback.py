"""The prompt's client of the running daemon (story 4q, AD-7, AD-8).

One place in the prompt speaks to the daemon. It sends a typed line to `4r`'s
prompt endpoint and, for a line the daemon acknowledged, reads that job's event
stream until its result arrives. Every endpoint, header and response field is
`4r`'s (`pm_ai.surfaces.api.app`) and `4e`'s token file; nothing here restates
them.

- **The standard library, to `127.0.0.1` only.** The surfaces contract forbids
  a third-party HTTP client here (`.importlinter`, `http-confined-to-adapters`),
  and loopback to the daemon is not egress (AD-8). A line's answer is read whole
  under one deadline and parsed with `http.client`, as `pm-ai doctor`'s probe
  does; a job's stream is read through `http.client` as it arrives.
- **The token is read on every request, never at open**, by the daemon's own
  rule (`pm_ai.domain.api_token`) plus this user's ownership, so a daemon
  started after the prompt opened is reached without a restart, and a file the
  daemon would refuse is named and never sent. The port is asked for on every
  request too.
- **Every answer is a value.** An inline answer, an acknowledgement, or one of
  the ways a line does not reach a command — no daemon, a rejected token, a busy
  daemon with its wait, an answer this client does not know, or a line not sent
  at all. Each of the last carries the one sentence the prompt prints. Nothing
  is retried, and no exception but Ctrl-C leaves `send` or `follow`.
"""

from __future__ import annotations

import errno
import http.client
import io
import json
import os
import socket
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from pm_ai.core.config import ConfigRefused
from pm_ai.domain.api_token import (
    MAX_TOKEN_FILE_BYTES,
    ApiTokenRefused,
    check_api_token,
    symlinked,
)
from pm_ai.surfaces.api.app import IDENTITY_HEADER, JOB_EVENTS_PATH, PROMPT_PATH

__all__ = [
    "Acknowledged",
    "Answer",
    "Busy",
    "CONNECT_SECONDS",
    "FINISHED",
    "LOOPBACK",
    "LoopbackClient",
    "MAX_ANSWER_BYTES",
    "MAX_EVENT_LINE_BYTES",
    "NoDaemon",
    "NotSent",
    "PROMPT_ANSWER_SECONDS",
    "StreamLost",
    "TokenRejected",
    "UnknownAnswer",
    "WaitAbandoned",
]

LOOPBACK = "127.0.0.1"
"""The one address this client connects to (AD-2, AD-8)."""

CONNECT_SECONDS = 2.0
"""How long a connection to the loopback port may take. Loopback answers in milliseconds."""

PROMPT_ANSWER_SECONDS = 30.0
"""How long the prompt waits for the daemon's whole answer to one line — one deadline.

An inline line is under five seconds by its declared bound (AD-21) and an
acknowledgement is immediate, so this is only reached by a daemon that stopped
answering. Ctrl-C abandons the wait sooner.
"""

MAX_ANSWER_BYTES = 16 * 1024 * 1024
"""The most of one answer this client reads; a larger one is named, not parsed."""

MAX_EVENT_LINE_BYTES = 16 * 1024 * 1024
"""The longest line of a job's stream this client reads."""

FINISHED = frozenset({"done", "failed"})
"""The states that end a job's stream: its result is in the event."""
_EXIT_UNEXPECTED = 1


# ── The outcomes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Answer:
    """The command ran: its printed text (stdout, then stderr) and its exit code."""

    output: str
    code: int


@dataclass(frozen=True, slots=True)
class Acknowledged:
    """The daemon took the line as a job; its result follows on the stream."""

    job_id: str


@dataclass(frozen=True, slots=True)
class NoDaemon:
    """No daemon answered: no token file yet, or nothing listens on the configured port."""

    sentence: str

    @classmethod
    def no_token(cls, path: Path) -> NoDaemon:
        return cls(
            f"no pm-ai daemon is running (there is no token file at {path} yet), "
            f"so this line was not run; start one with `pm-ai daemon run` in "
            f"another terminal."
        )

    @classmethod
    def refused(cls, port: int) -> NoDaemon:
        return cls(
            f"nothing answers on {LOOPBACK}:{port}, the port in config.toml, so "
            f"this line was not run; start the daemon with `pm-ai daemon run` — "
            f"a daemon started on another port needs restarting to use this one."
        )


def _rejected(path: Path) -> str:
    return (
        f"the daemon rejected the token in {path}, so the token file and the "
        f"daemon disagree; restart the daemon (Ctrl-C in its terminal, then "
        f"`pm-ai daemon run`)"
    )


@dataclass(frozen=True, slots=True)
class TokenRejected:
    """The daemon answered `401`: the token file and the daemon disagree."""

    token_path: Path

    @property
    def sentence(self) -> str:
        return _rejected(self.token_path) + "."


@dataclass(frozen=True, slots=True)
class Busy:
    """The daemon answered `503`: busy. `seconds` from `Retry-After`; `None` when it gave a date."""

    seconds: int | None

    @property
    def sentence(self) -> str:
        if self.seconds is None:
            when = "shortly"
        elif self.seconds == 0:
            when = "now"
        else:
            when = f"in {self.seconds} {'second' if self.seconds == 1 else 'seconds'}"
        return f"the daemon is busy, so this line was not run; try again {when}."


@dataclass(frozen=True, slots=True)
class UnknownAnswer:
    """An answer this client does not know, or none at all after the line was sent.

    `status` is the HTTP status when one arrived; `why` completes the sentence —
    after the status when there is one, as a whole clause when there is not.
    """

    status: int | None
    why: str

    @property
    def sentence(self) -> str:
        if self.status is None:
            return f"{self.why}, so whether this line ran is not known."
        return (
            f"the daemon answered HTTP {self.status}{self.why}, an answer this "
            f"prompt does not know, so whether this line ran is not known; if the "
            f"daemon is an older pm-ai, restart it with this one."
        )


@dataclass(frozen=True, slots=True)
class NotSent:
    """The line was never sent: the token file, `daemon_port`, or the port itself failed."""

    sentence: str


@dataclass(frozen=True, slots=True)
class StreamLost:
    """The job's result could not be read: its stream failed, or ended first."""

    job_id: str
    why: str

    @property
    def sentence(self) -> str:
        return (
            f"the result of {self.job_id} could not be read: {self.why}. The "
            f"daemon accepted it and may still finish it, but its result is not "
            f"shown here."
        )


class WaitAbandoned(KeyboardInterrupt):
    """Ctrl-C during `send`. `sent` says whether the line had reached the daemon."""

    def __init__(self, *, sent: bool) -> None:
        super().__init__()
        self.sent = sent


Unreached = NoDaemon | TokenRejected | Busy | UnknownAnswer | NotSent
"""Every way a line does not reach a command. Each has a `sentence`."""

Outcome = Answer | Acknowledged | Unreached
"""What `LoopbackClient.send` returns."""

Followed = Answer | StreamLost
"""What `LoopbackClient.follow` returns."""


@dataclass(frozen=True, slots=True)
class _Blocked:
    """Why no request can be made: no token file yet (`no_token`), or `reason`."""

    reason: str
    no_token: Path | None = None

    def for_line(self) -> NoDaemon | NotSent:
        if self.no_token is not None:
            return NoDaemon.no_token(self.no_token)
        return NotSent(f"{self.reason}, so this line was not sent.")


class _Malformed(Exception):
    """The stream carried something that is not a job's event."""


class _Progress:
    sent = False


# ── The client ───────────────────────────────────────────────────────────────


class LoopbackClient:
    """`4r`'s prompt endpoint and job stream, over loopback, with `4e`'s token.

    `token_path` and `port` are asked on every request: the composition root
    binds them to `~/.pm-ai/private/api_token` and to `daemon_port` in
    `config.toml`, so nothing is read when the prompt opens.
    """

    def __init__(
        self,
        *,
        token_path: Callable[[], Path],
        port: Callable[[], int],
        answer_seconds: float = PROMPT_ANSWER_SECONDS,
    ) -> None:
        self._token_path = token_path
        self._port = port
        self._answer_seconds = answer_seconds

    def send(self, line: str) -> Outcome:
        """`POST` the line to the prompt endpoint; the answer as a value.

        Ctrl-C raises `WaitAbandoned`, saying whether the line had been sent; the
        daemon finishes a sent line on its own. Nothing else is raised.
        """
        progress = _Progress()
        try:
            return self._send(line, progress)
        except KeyboardInterrupt as interrupted:
            raise WaitAbandoned(sent=progress.sent) from interrupted
        except Exception as error:  # noqa: BLE001 — named, never the end of the session
            if progress.sent:
                return UnknownAnswer(
                    None,
                    f"reading the daemon's answer stopped on an unexpected "
                    f"{type(error).__name__}",
                )
            return NotSent(
                f"sending the line stopped on an unexpected {type(error).__name__}, "
                f"so this line was not sent."
            )

    def reachable(self) -> NoDaemon | NotSent | None:
        """`None` when a daemon listens and a request could be made; else why not.

        Asked by the prompt before it asks a `transcript drop`'s questions
        (story 11d), so a line that cannot be sent asks nothing first. The
        token and port are read, and a connection is opened and closed at
        once; nothing is sent. A daemon that stops after this answer is met by
        `send` as usual.
        """
        try:
            ready = self._ready()
            if isinstance(ready, _Blocked):
                return ready.for_line()
            port = ready[0]
            try:
                connection = socket.create_connection((LOOPBACK, port), timeout=CONNECT_SECONDS)
            except ConnectionRefusedError:
                return NoDaemon.refused(port)
            except OSError as unreachable:
                return NotSent(
                    f"{LOOPBACK}:{port} could not be reached ({_named(unreachable)}), "
                    f"so this line was not sent."
                )
            connection.close()
        except Exception as error:  # noqa: BLE001 — named, never the end of the session
            return NotSent(
                f"whether the daemon answers could not be told ({type(error).__name__}), "
                f"so this line was not sent."
            )
        return None

    def follow(
        self, job_id: str, on_state: Callable[[str], None] | None = None
    ) -> Followed:
        """Read the job's event stream until it is `done` or `failed`; its result.

        Waits as long as the job runs — the thread that calls this only waits
        for a job the daemon already owns. Any way the stream ends first is
        `StreamLost`, naming the job. Nothing is raised.

        `on_state` (story 4v) is told the state of each event read, in order,
        the finished one included, before the result is returned. An error in
        it is dropped and the stream still read: reporting a state never ends
        the wait. The stream, its endpoint and its events are `4r`'s, unchanged.
        """
        try:
            return self._follow(job_id, on_state)
        except Exception as error:  # noqa: BLE001 — named, never the end of the session
            return StreamLost(job_id, f"reading its stream stopped on an unexpected {type(error).__name__}")

    # ── the line ─────────────────────────────────────────────────────────────

    def _send(self, line: str, progress: _Progress) -> Outcome:
        ready = self._ready()
        if isinstance(ready, _Blocked):
            return ready.for_line()
        port, token, token_path = ready
        address = f"{LOOPBACK}:{port}"
        body = json.dumps({"line": line}).encode("utf-8")
        request = (
            f"POST {PROMPT_PATH} HTTP/1.1\r\nHost: {address}\r\n"
            f"Authorization: Bearer {token}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode("ascii") + body
        try:
            connection = socket.create_connection((LOOPBACK, port), timeout=CONNECT_SECONDS)
        except ConnectionRefusedError:
            return NoDaemon.refused(port)
        except TimeoutError:
            return NotSent(
                f"nothing answered on {address} within {CONNECT_SECONDS:g} seconds of "
                f"connecting, so this line was not sent; if a daemon is running "
                f"there and has stopped responding, restart it."
            )
        except OSError as unreachable:
            return NotSent(
                f"{address} could not be reached ({_named(unreachable)}), so this "
                f"line was not sent."
            )
        received = bytearray()
        with connection:
            # One deadline for the whole answer, however the daemon paces it.
            deadline = time.monotonic() + self._answer_seconds
            try:
                connection.settimeout(self._answer_seconds)
                connection.sendall(request)
                progress.sent = True
                while len(received) <= MAX_ANSWER_BYTES:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise TimeoutError
                    connection.settimeout(left)
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    received += chunk
            except TimeoutError:
                return UnknownAnswer(
                    None,
                    f"the daemon on {address} sent no whole answer within "
                    f"{self._answer_seconds:g} seconds; it may still finish the line",
                )
            except OSError as unreachable:
                return UnknownAnswer(
                    None, f"the daemon on {address} could not be asked ({_named(unreachable)})"
                )
        if len(received) > MAX_ANSWER_BYTES:
            return UnknownAnswer(
                None,
                f"the daemon on {address} sent an answer larger than "
                f"{MAX_ANSWER_BYTES // (1024 * 1024)} MiB, which was not read",
            )
        response = http.client.HTTPResponse(_Received(bytes(received)))  # type: ignore[arg-type]
        try:
            response.begin()
            raw = response.read()
        except (http.client.HTTPException, ValueError) as garbled:
            return UnknownAnswer(
                None,
                f"the daemon on {address} did not answer as HTTP ({type(garbled).__name__})",
            )
        return _judge(response, raw, token_path)

    # ── the job's stream ─────────────────────────────────────────────────────

    def _follow(self, job_id: str, on_state: Callable[[str], None] | None) -> Followed:
        ready = self._ready()
        if isinstance(ready, _Blocked):
            # The job was accepted; only reading its result is blocked.
            reason = (
                f"there is no token file at {ready.no_token} any more"
                if ready.no_token is not None
                else ready.reason
            )
            return StreamLost(job_id, reason)
        port, token, token_path = ready
        address = f"{LOOPBACK}:{port}"
        connection = http.client.HTTPConnection(LOOPBACK, port, timeout=CONNECT_SECONDS)
        try:
            try:
                connection.connect()
            except ConnectionRefusedError:
                return StreamLost(job_id, f"nothing answers on {address} any more")
            except TimeoutError:
                return StreamLost(
                    job_id, f"nothing answered on {address} within {CONNECT_SECONDS:g} seconds"
                )
            # Widened before the request: a daemon slow to send the headers is
            # still answering, and the stream then waits as long as the job.
            if connection.sock is not None:
                connection.sock.settimeout(None)
            connection.request(
                "GET",
                JOB_EVENTS_PATH.format(job_id=job_id),
                headers={"Authorization": f"Bearer {token}"},
            )
            response = connection.getresponse()
            if response.getheader(IDENTITY_HEADER) is None:
                return StreamLost(job_id, f"something that is not a pm-ai daemon answered on {address}")
            if response.status == 401:
                return StreamLost(job_id, _rejected(token_path))
            if response.status != 200:
                return StreamLost(job_id, f"the daemon answered HTTP {response.status}")
            for event in _events(response):
                state = event.get("state")
                if on_state is not None and isinstance(state, str):
                    _report(on_state, state)
                if state in FINISHED:
                    return _result(job_id, event)
        except _Malformed as malformed:
            return StreamLost(job_id, str(malformed))
        except (OSError, http.client.HTTPException) as lost:
            return StreamLost(job_id, _named(lost))
        finally:
            connection.close()
        return StreamLost(job_id, "the daemon closed its stream before the job finished")

    # ── what every request needs first ───────────────────────────────────────

    def _ready(self) -> tuple[int, str, Path] | _Blocked:
        """The port and the token, read now; or why no request can be made."""
        try:
            port = self._port()
        except (ConfigRefused, OSError) as unreadable:
            return _Blocked(
                f"`daemon_port` could not be read from ~/.pm-ai/config.toml "
                f"({_named(unreadable)})"
            )
        try:
            path = self._token_path()
        except Exception as error:  # noqa: BLE001 — named, never raised
            return _Blocked(
                f"where the token file is could not be worked out "
                f"({type(error).__name__})"
            )
        try:
            token = _read_token(path)
        except FileNotFoundError:
            return _Blocked(f"there is no token file at {path} yet", no_token=path)
        except ApiTokenRefused as unusable:
            return _Blocked(str(unusable).rstrip("."))
        except OSError as unreadable:
            return _Blocked(
                f"the token file {path} could not be read "
                f"({unreadable.strerror or unreadable})"
            )
        return port, token, path


def _report(on_state: Callable[[str], None], state: str) -> None:
    """Tell `on_state` a state; an error in it is dropped, never the end of the wait."""
    try:
        on_state(state)
    except Exception:  # noqa: BLE001, S110 — a failed report never ends the wait (story 4v)
        pass


def _read_token(path: Path) -> str:
    """The token in `path`: this user's own regular file, judged by the daemon's rule.

    Never followed through a link. A file another account owns is refused —
    whoever owns it can change it, and the daemon this user runs never wrote it.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as unopenable:
        if unopenable.errno == errno.ELOOP:
            raise symlinked(str(path)) from unopenable
        raise
    try:
        status = os.fstat(descriptor)
        if status.st_uid != os.getuid():
            raise ApiTokenRefused(
                f"{path} belongs to another account (uid {status.st_uid}), not "
                f"this one, so it is not this user's token. Nothing was changed; "
                f"delete it and pm-ai mints a new one at the next daemon start."
            )
        raw = b""
        if stat.S_ISREG(status.st_mode):
            limit = MAX_TOKEN_FILE_BYTES + 1
            while len(raw) < limit and (chunk := os.read(descriptor, limit - len(raw))):
                raw += chunk
    finally:
        os.close(descriptor)
    return check_api_token(raw, mode=status.st_mode, where=str(path))


class _Received:
    """A finished answer, offered to `http.client.HTTPResponse` as its socket."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def makefile(self, mode: str, *args: object, **kwargs: object) -> io.BytesIO:
        return io.BytesIO(self._raw)


def _retry_after(value: str | None) -> Busy:
    """`Retry-After` as a wait: ASCII digits are seconds, missing is now, a date is shortly."""
    if value is None or not value.strip():
        return Busy(0)
    text = value.strip()
    if text.isascii() and text.isdigit():
        return Busy(int(text))
    return Busy(None)


def _judge(response: http.client.HTTPResponse, raw: bytes, token_path: Path) -> Outcome:
    """One `POST /v1/prompt` answer as a value."""
    status = response.status
    if response.getheader(IDENTITY_HEADER) is None:
        return UnknownAnswer(status, " from something that is not a pm-ai daemon")
    if status == 401:
        return TokenRejected(token_path)
    if status == 503:
        return _retry_after(response.getheader("Retry-After"))
    try:
        body = json.loads(raw)
    except ValueError:
        return UnknownAnswer(status, " with a body that is not JSON")
    if not isinstance(body, Mapping):
        return UnknownAnswer(status, " with a body that is not the answer to a line")
    if status == 200 and body.get("inline") is True:
        output, code = body.get("output"), body.get("exit")
        if isinstance(output, str) and type(code) is int:
            return Answer(output, code)
    if status == 202 and body.get("inline") is False:
        job_id = body.get("job_id")
        if isinstance(job_id, str) and job_id:
            return Acknowledged(job_id)
    if status in (200, 202):
        return UnknownAnswer(status, " with a body that is not the answer to a line")
    return UnknownAnswer(status, "")


def _events(response: http.client.HTTPResponse) -> Iterator[Mapping[str, object]]:
    """Each `data:` event on the stream, decoded, until the daemon closes it.

    An event still open when the stream closes — its `data:` line with no blank
    line after — is yielded too. `_Malformed` for a line too long to read or an
    event that is not a job's row.
    """
    data: list[str] = []

    def event() -> Mapping[str, object]:
        try:
            decoded = json.loads("\n".join(data))
        except ValueError as unreadable:
            raise _Malformed("the daemon sent an event that is not JSON") from unreadable
        if not isinstance(decoded, Mapping):
            raise _Malformed("the daemon sent an event that is not a job's row")
        return decoded

    while True:
        line = response.readline(MAX_EVENT_LINE_BYTES + 1)
        if len(line) > MAX_EVENT_LINE_BYTES:
            raise _Malformed(
                f"the daemon sent a stream line longer than "
                f"{MAX_EVENT_LINE_BYTES // (1024 * 1024)} MiB, which was not read"
            )
        if not line:
            if data:
                yield event()
            return
        try:
            text = line.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as unreadable:
            raise _Malformed("the daemon sent a stream line that is not UTF-8") from unreadable
        if text:
            if text.startswith("data:"):
                data.append(text[len("data:"):].lstrip(" "))
            continue
        if data:
            yield event()
            data = []


def _result(job_id: str, event: Mapping[str, object]) -> Followed:
    """A finished job's row as the command's answer.

    `done` carries the command's output and exit code. `failed` carries pm-ai's
    own sentence and no exit code — the command did not finish — so it is shown
    as that sentence and exit 1, the code for a command that stopped
    unexpectedly; no new code is invented for it.
    """
    result = event.get("result")
    if not isinstance(result, Mapping):
        return StreamLost(job_id, f"its {event.get('state')} event carried no result")
    if event.get("state") == "done":
        output, code = result.get("output"), result.get("exit")
        if isinstance(output, str) and type(code) is int:
            return Answer(output, code)
        return StreamLost(job_id, "its result was not a command's output and exit code")
    error = result.get("error")
    sentence = error if isinstance(error, str) and error else "the job failed"
    return Answer(f"pm-ai: {sentence}\n", _EXIT_UNEXPECTED)


def _named(error: BaseException) -> str:
    """An error as a clause: its words where it has them, its type where it has none."""
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    text = str(error)
    return text if text else type(error).__name__
