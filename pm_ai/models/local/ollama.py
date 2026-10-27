"""The local model behind `ModelPort`: Ollama on loopback, and only there (AD-1 class LM).

**Nothing can leave the machine through this adapter, by construction.** It
does not use the `ollama` Python package: 0.6.2 reads `OLLAMA_HOST` and
`OLLAMA_API_KEY` from the environment, adds the key as a bearer header, and
follows redirects by default. It builds its own `httpx.Client`:

- the base URL is the constant `BASE_URL`, `http://127.0.0.1:11434` — there is
  no host setting anywhere, and `config.toml` refuses a key naming one;
- `trust_env=False`, so no proxy and no setting is read from the environment;
- `follow_redirects=False`, and a redirect is refused naming the status;
- no authorization header.

**A deadline and an abort really end the request.** Closing an `httpx.Client`
does not interrupt a request another thread is waiting on, and httpx's
timeouts apply per phase (connect, write, each read), so three of them can
add up past a call's deadline. So the real transport runs on
`LoopbackBackend`, an httpcore network backend that records every socket it
opens: a watchdog timer shuts those sockets down (`SHUT_RDWR`) when the call's
*total* deadline passes, and `close()` — the daemon's shutdown — does the same
and refuses every later connection. Connecting gets `CONNECT_TIMEOUT_SECONDS`
of its own: the server is on loopback, so a slow connect is a stopped server.

`transport` exists for tests (`httpx.MockTransport`); wiring never passes one.

**No model that runs elsewhere.** The local server forwards a cloud model to
Ollama's hosted service, and an answer naming `remote_host` or `remote_model`
arrives only after the prompt has left. So every call asks `/api/show` first
and refuses by name a model whose answer names either, or whose tag is a cloud
tag; the chat answer's own fields are checked again as a backstop.

**Every request asks the same way:** `keep_alive: 0`, temperature 0, the fixed
`SEED`, the instance's `window` (`config.toml`'s `local_model_window`, story
7g), the output cap `NUM_PREDICT`, `truncate: false` (measured at build on Ollama
0.40.6: without it an over-long prompt is shortened silently to fit and the
answer's counts stay below the window, so the count check below cannot see
it; with it the server refuses the prompt with a 400), and `think: false` only
for a model whose `/api/show` lists thinking among its capabilities. An answer
is refused (`PossiblyTruncated`) when a token count is missing or when
`done_reason` is not `stop`, and as `WindowReached` (a kind of it) when its
prompt and output tokens together reach the window or the server refuses the
prompt as too long for it; one that stops at the output cap is refused as
`OutputCapReached`, with a warning in the log naming the model, the cap and
the task class — never the text.

**The presence check** (`local_model_presence`) is the one other kind of
request: which models are pulled (`/api/tags`) and whether the named one runs
locally and can complete (`/api/show`). No prompt text. `setup` and `doctor`
reach it through the composition root. It never raises.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import weakref
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpcore
import httpx

from pm_ai.domain.health import Health, Probe
from pm_ai.domain.model_calls import (
    DEFAULT_LOCAL_MODEL_WINDOW,
    LOCAL_MODEL_PROBE,
    LOCAL_MODEL_WINDOWS,
    SERVER_LIMITS,
    START_OLLAMA,
    LocalModelPresence,
    ModelAnswer,
    ScopeFlow,
    assert_flow_legal,
    is_cloud_tag,
    normalize_model_name,
    unset_local_model,
)
from pm_ai.domain.sanitize import Sanitized
from pm_ai.domain.task_classes import TaskClass
from pm_ai.ports import (
    LocalModelUnavailable,
    ModelTimedOut,
    OutputCapReached,
    PossiblyTruncated,
    RemoteModelRefused,
    WindowReached,
)

__all__ = [
    "ADDRESS",
    "BASE_URL",
    "CONNECT_TIMEOUT_SECONDS",
    "LOCAL_ONLY_CLASSES",
    "MODEL_CALL_DEADLINE_SECONDS",
    "NUM_PREDICT",
    "PRESENCE_TIMEOUT_SECONDS",
    "SEED",
    "LoopbackBackend",
    "NotALocalClass",
    "OllamaModel",
    "local_model_presence",
]

ADDRESS = "127.0.0.1:11434"
BASE_URL = f"http://{ADDRESS}"
"""The only address this adapter can reach. A constant, never a setting."""

NUM_PREDICT = 4096
"""The output cap every request sends (`num_predict`); raised from 2,048 by `7b`.

`7a` sized it from the longest valid answer it measured — 314 output tokens
(`qwen3:8b`, the authored meeting). For `7b`'s answer shape the cap was raised
to 4,096 (Andrei, 2026-10-19); `7b`'s real answers measured at most 716 output
tokens. The shape's limits do not guarantee the largest valid answer fits
(Andrei, 2026-10-20): an answer that reaches the cap is refused as
`OutputCapReached`, asked for once more, then refused. An answer that does not end
still stops here rather than holding the model pool's one slot to the
deadline. With the cap, a prompt has 28,672 tokens at a window of 32,768 and
12,288 at 16,384.

The window itself is a setting (`local_model_window`, story 7g), passed to the
adapter by the composition root. 16,384 was the window from `7a` (the
real-length recording measured 8,619 prompt tokens on `qwen3:8b`); at the first
end-to-end test 3 of 7 hour-long meetings did not fit it. Measured 2026-10-27
on an 18 GB M3 Pro with `qwen3:8b` and a fictional transcript: at 32,768
Ollama peaked at 9.4–9.7 GB (7.0 GB at 16,384), an 18,000-token prompt took
116 s and a 27,000-token one 182 s against the 300-second deadline, and memory
pressure reached warning.
"""

_log = logging.getLogger(__name__)

SEED = 7
"""The fixed seed every request sends, with temperature 0."""

MODEL_CALL_DEADLINE_SECONDS = 300.0
"""A call's total deadline: half of CAP-3's 600 seconds for fetch, sanitize and parse."""

CONNECT_TIMEOUT_SECONDS = 2.0
"""Connecting to loopback: past this, nothing is answering."""

PRESENCE_TIMEOUT_SECONDS = 3.0
"""The presence check's bound, per request: `doctor` and `setup` wait no longer."""

CONTEXT_REFUSAL = "exceeds the available context size"
"""What Ollama 0.40.6 answers (400) for a prompt longer than the window under `truncate: false`.

Observed at build: `request (2018 tokens) exceeds the available context size
(512 tokens), try increasing it`.
"""

LOCAL_ONLY_CLASSES = frozenset(
    {
        TaskClass.TRANSCRIPTION,
        TaskClass.EXTRACTION,
        TaskClass.CLASSIFICATION,
        TaskClass.EMBEDDING,
        TaskClass.FUZZY_MATCH,
        TaskClass.HEADER_READING,
    }
)
"""AD-15's local-only classes — the only ones this adapter answers."""

_ERROR_TEXT_LIMIT = 200
"""How much of a server's error body a refusal quotes."""


class NotALocalClass(ValueError):
    """A frontier-eligible class reached the local adapter; no request was made.

    A wiring error rather than a refusal a caller handles: the router (`7c`)
    routes every class to the side its table declares, so no caller of the
    port meets this.
    """


# ── The transport: loopback, with sockets an abort can reach ─────────────────


class LoopbackBackend(httpcore.SyncBackend):
    """httpcore's own synchronous backend, recording the sockets it opens.

    `cut()` shuts every open socket down, so a request blocked reading one
    ends at once — closing a client does not. `close()` cuts and refuses every
    later connection. `port` is a test seam: it moves the connection to another
    *loopback* port (a test's own socket server), never to another host; the
    URL the client asks for stays `BASE_URL`.
    """

    def __init__(self, *, port: int | None = None) -> None:
        super().__init__()
        self._port = port
        self._lock = threading.Lock()
        self._sockets: weakref.WeakSet[socket.socket] = weakref.WeakSet()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        if self._closed:
            raise httpcore.ConnectError("the local model's connections were closed")
        if self._port is not None:
            host, port = "127.0.0.1", self._port
        stream = super().connect_tcp(
            host, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )
        opened = stream.get_extra_info("socket")
        with self._lock:
            if isinstance(opened, socket.socket):
                self._sockets.add(opened)
            if self._closed:
                self._shutdown(opened)
        return stream

    def cut(self) -> None:
        """Break every open connection now; later connections are still allowed."""
        with self._lock:
            for opened in list(self._sockets):
                self._shutdown(opened)

    def close(self) -> None:
        """Break every open connection and refuse every later one."""
        with self._lock:
            self._closed = True
            for opened in list(self._sockets):
                self._shutdown(opened)

    @staticmethod
    def _shutdown(opened: object) -> None:
        if isinstance(opened, socket.socket):
            try:
                opened.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # already closed or never connected: nothing to break


def _loopback_transport(backend: LoopbackBackend) -> httpx.BaseTransport:
    """httpx's own transport on `backend`, with no proxy and no environment.

    `HTTPTransport` takes no network backend, so its connection pool is
    rebuilt with one — the same settings `HTTPTransport` would give it. This
    leans on `httpx` 0.28.1's `_pool` attribute, which the pin holds and the
    real-socket tests in `tests/models/test_ollama_adapter.py` exercise.
    """
    transport = httpx.HTTPTransport(trust_env=False)
    transport._pool = httpcore.ConnectionPool(  # noqa: SLF001 — see the docstring
        network_backend=backend,
        max_connections=4,
        retries=0,
    )
    return transport


def _default_transport(backend: LoopbackBackend) -> httpx.BaseTransport:
    """The real transport. A function so the test suite can replace it.

    `tests/conftest.py` replaces it, so no test reaches a real Ollama through
    wiring.
    """
    return _loopback_transport(backend)


def _client(transport: httpx.BaseTransport) -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        trust_env=False,
        follow_redirects=False,
        transport=transport,
    )


class _Watchdog:
    """Cuts the backend's connections when a request's total time is up."""

    def __init__(self, backend: LoopbackBackend, seconds: float) -> None:
        self.fired = False
        self._backend = backend
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def _fire(self) -> None:
        self.fired = True
        self._backend.cut()

    def __enter__(self) -> _Watchdog:
        self._timer.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._timer.cancel()


def _timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(seconds, connect=min(CONNECT_TIMEOUT_SECONDS, seconds))


# ── Wording ──────────────────────────────────────────────────────────────────


def _quoted(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _ERROR_TEXT_LIMIT else f"{text[:_ERROR_TEXT_LIMIT]}…"


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return _quoted(response.text or "an empty body")
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return _quoted(body["error"])
    return _quoted(response.text)


def _remote_fields(body: Mapping[str, Any]) -> list[str]:
    return [f"{key}={body[key]!r}" for key in ("remote_host", "remote_model") if body.get(key)]


def _pull(name: str) -> str:
    return f"Pull it with `ollama pull {name}` — pm-ai never downloads a model."


def _cloud_sentence(name: str) -> str:
    return (
        f"{name} has a cloud tag: the Ollama server forwards it to Ollama's hosted "
        f"service, so the prompt would leave this machine. pm-ai sends prompts only "
        f"to a model that runs locally (AD-1 class LM). Name a local model, and set "
        f"OLLAMA_NO_CLOUD=1 to turn cloud models off on the server."
    )


def _remote_sentence(name: str, fields: Sequence[str]) -> str:
    return (
        f"the Ollama server answered for {name} naming {', '.join(fields)}: it runs "
        f"the model elsewhere, so the prompt would leave this machine. pm-ai sends "
        f"prompts only to a model that runs locally (AD-1 class LM). Name a local "
        f"model, and set OLLAMA_NO_CLOUD=1 to turn cloud models off on the server."
    )


@dataclass(frozen=True, slots=True)
class _Show:
    capabilities: frozenset[str]


# ── The adapter ──────────────────────────────────────────────────────────────


class OllamaModel:
    """`ModelPort` over the local Ollama server, for AD-15's local-only classes.

    Constructing it opens nothing: the client is built on the first request,
    so a short command that composes a daemon pays nothing for it. `close` —
    which the daemon's model pool calls at shutdown, running job or not —
    breaks any connection in flight and refuses every later call. `window` is
    the context window every request asks for and every size check compares
    against: one of `LOCAL_MODEL_WINDOWS`, anything else refused.
    """

    def __init__(
        self,
        model: str,
        *,
        window: int = DEFAULT_LOCAL_MODEL_WINDOW,
        deadline_seconds: float = MODEL_CALL_DEADLINE_SECONDS,
        transport: httpx.BaseTransport | None = None,
        backend: LoopbackBackend | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        name = normalize_model_name(model)
        if not name:
            raise ValueError("OllamaModel needs a model name; an unset local_model wires none.")
        if (
            isinstance(window, bool)
            or not isinstance(window, int)
            or window not in LOCAL_MODEL_WINDOWS
        ):
            raise ValueError(
                f"the local model's window is {LOCAL_MODEL_WINDOWS[0]} or "
                f"{LOCAL_MODEL_WINDOWS[1]} tokens, not {window!r}."
            )
        if deadline_seconds <= 0:
            raise ValueError(f"a deadline is a positive number of seconds, not {deadline_seconds!r}.")
        self._name = name
        self._window = int(window)
        self._deadline = float(deadline_seconds)
        self._clock = clock
        self._backend = backend if backend is not None else LoopbackBackend()
        self._transport = transport
        self._lock = threading.Lock()
        self._http: httpx.Client | None = None
        self._closed = False

    @property
    def name(self) -> str:
        """The model's name, as compared: a name without a tag is `:latest`."""
        return self._name

    @property
    def window(self) -> int:
        """The context window every request sends as `num_ctx` (story 7g)."""
        return self._window

    @property
    def deadline_seconds(self) -> float:
        return self._deadline

    @property
    def opened(self) -> bool:
        """Whether a client has been built — false until the first request."""
        return self._http is not None

    def close(self) -> None:
        """Break any connection in flight, close the client, refuse every later call."""
        with self._lock:
            self._closed = True
            http = self._http
        self._backend.close()
        if http is not None:
            http.close()

    def _client(self) -> httpx.Client:
        with self._lock:
            if self._closed:
                raise _Abandoned()
            if self._http is None:
                transport = (
                    self._transport
                    if self._transport is not None
                    else _default_transport(self._backend)
                )
                self._http = _client(transport)
            return self._http

    # ── The call ─────────────────────────────────────────────────────────────

    def complete(
        self,
        *,
        task_class: TaskClass,
        instructions: str,
        external: Sequence[Sanitized],
        flow: ScopeFlow,
        shape: Mapping[str, object] | None = None,
    ) -> ModelAnswer:
        """One request to the local model, refused by name before it leaves when it may not.

        In order, and each before any request: the flow (`ScopeFlowRefused`),
        the task class (`NotALocalClass`), a cloud tag (`RemoteModelRefused`).
        Then `/api/show` (remote fields refused; thinking read), then the chat,
        both inside one total deadline. Only `fragment.for_model` is put in
        the prompt.
        """
        assert_flow_legal(flow)
        if task_class not in LOCAL_ONLY_CLASSES:
            raise NotALocalClass(
                f"{task_class.value} is not a local-only class (AD-15), so the local "
                f"model does not answer it and no request was made. The router "
                f"routes it (story 7c)."
            )
        if is_cloud_tag(self._name):
            raise RemoteModelRefused(_cloud_sentence(self._name))
        user = "\n\n".join(fragment.for_model for fragment in external)
        started = self._clock()
        show = self._show(started)
        request: dict[str, Any] = {
            "model": self._name,
            "stream": False,
            "keep_alive": 0,
            "truncate": False,
            "options": {
                "temperature": 0,
                "seed": SEED,
                "num_ctx": self._window,
                "num_predict": NUM_PREDICT,
            },
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user},
            ],
        }
        if "thinking" in show.capabilities:
            request["think"] = False
        if shape is not None:
            request["format"] = dict(shape)
        body = self._post("/api/chat", request, started=started)
        return self._answer(body, task_class)

    def _remaining(self, started: float) -> float:
        left = self._deadline - (self._clock() - started)
        if left <= 0:
            raise ModelTimedOut(
                f"the call to {self._name} outlasted its {self._deadline:g}-second "
                f"deadline before its request was sent; nothing was written."
            )
        return left

    def _show(self, started: float) -> _Show:
        body = self._post("/api/show", {"model": self._name}, started=started)
        fields = _remote_fields(body)
        if fields:
            raise RemoteModelRefused(_remote_sentence(self._name, fields))
        capabilities = body.get("capabilities") or []
        if not isinstance(capabilities, list):
            raise LocalModelUnavailable(
                f"the Ollama server's /api/show answer for {self._name} lists its "
                f"capabilities as {type(capabilities).__name__}, not a list; refused "
                f"rather than guessed."
            )
        return _Show(frozenset(c for c in capabilities if isinstance(c, str)))

    def _abandoned(self) -> LocalModelUnavailable:
        return LocalModelUnavailable(
            f"the call to {self._name} was abandoned: the daemon is stopping, so its "
            f"connection was closed and nothing was written."
        )

    def _post(self, path: str, payload: Mapping[str, Any], *, started: float) -> dict[str, Any]:
        timeout = self._remaining(started)
        watchdog = _Watchdog(self._backend, timeout)
        try:
            http = self._client()
            with watchdog:
                response = http.post(path, json=payload, timeout=_timeout(timeout))
        except _Abandoned as closed:
            raise self._abandoned() from closed
        except (httpx.HTTPError, RuntimeError) as failed:
            # Shutdown first, then the deadline: either is why the socket broke,
            # and the error httpx raised for it is only the symptom.
            if self._closed:
                raise self._abandoned() from failed
            if watchdog.fired or (
                isinstance(failed, httpx.TimeoutException)
                and not isinstance(failed, httpx.ConnectTimeout)
            ):
                raise ModelTimedOut(
                    f"{self._name} did not answer {path} within its {self._deadline:g}-"
                    f"second deadline; the connection was closed and nothing was written."
                ) from failed
            if isinstance(failed, httpx.ConnectTimeout):
                raise LocalModelUnavailable(
                    f"nothing answered on {ADDRESS} within {CONNECT_TIMEOUT_SECONDS:g} "
                    f"seconds. {START_OLLAMA}"
                ) from failed
            if isinstance(failed, httpx.ConnectError):
                raise LocalModelUnavailable(
                    f"nothing is listening on {ADDRESS}, so the local model could not be "
                    f"asked. {START_OLLAMA}"
                ) from failed
            raise LocalModelUnavailable(
                f"the request to the Ollama server on {ADDRESS} failed "
                f"({type(failed).__name__}: {_quoted(str(failed))}); not retried."
            ) from failed
        return self._body(path, response)

    def _body(self, path: str, response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if response.is_redirect or 300 <= status < 400:
            where = response.headers.get("location", "nowhere it named")
            raise LocalModelUnavailable(
                f"the Ollama server answered {path} with {status}, a redirect to "
                f"{_quoted(where)}. pm-ai follows no redirect from the local model "
                f"server; refused."
            )
        if status == 503:
            raise LocalModelUnavailable(
                f"the Ollama server answered {path} with 503: it is overloaded. "
                f"Refused, not retried: {_error_text(response)}"
            )
        if status == 404 and path in ("/api/show", "/api/chat"):
            raise LocalModelUnavailable(
                f"{self._name} is not pulled on this machine's Ollama server "
                f"({_error_text(response)}). {_pull(self._name)}"
            )
        if status == 400 and CONTEXT_REFUSAL in _error_text(response).lower():
            raise WindowReached(
                f"the prompt does not fit {self._name}'s {self._window}-token window, and "
                f"the server refused it rather than shorten it: {_error_text(response)}"
            )
        if status != 200:
            raise LocalModelUnavailable(
                f"the Ollama server answered {path} with {status}: "
                f"{_error_text(response)}. Not retried."
            )
        try:
            body = response.json()
        except ValueError as malformed:
            raise LocalModelUnavailable(
                f"the Ollama server's answer to {path} is not JSON: "
                f"{_quoted(response.text or 'an empty body')}"
            ) from malformed
        if not isinstance(body, dict):
            raise LocalModelUnavailable(
                f"the Ollama server's answer to {path} is {type(body).__name__}, not "
                f"an object: {_quoted(response.text)}"
            )
        return body

    def _answer(self, body: Mapping[str, Any], task_class: TaskClass) -> ModelAnswer:
        fields = _remote_fields(body)
        if fields:
            raise RemoteModelRefused(
                f"{_remote_sentence(self._name, fields)} The answer arrived after the "
                f"prompt had left, and was discarded."
            )
        counts: dict[str, int] = {}
        for field in ("prompt_eval_count", "eval_count"):
            value = body.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PossiblyTruncated(
                    f"{self._name}'s answer carries no usable {field} ({value!r}), so "
                    f"whether it reached the {self._window}-token window cannot be told; "
                    f"discarded."
                )
            counts[field] = value
        reason = body.get("done_reason")
        prompt, output = counts["prompt_eval_count"], counts["eval_count"]
        if reason == "length" and output >= NUM_PREDICT and prompt + output < self._window:
            _log.warning(
                "the local model %s reached its output cap of %d tokens (task "
                "class %s); the answer was discarded",
                self._name,
                NUM_PREDICT,
                task_class.value,
            )
            raise OutputCapReached(
                f"{self._name} reached its {NUM_PREDICT}-token output cap (task "
                f"class {task_class.value}), so the answer may be cut short or never "
                f"ended; discarded."
            )
        # The window before the stop reason: an answer that reaches the window
        # is a size refusal whatever reason the server gave (`length`, usually).
        if prompt + output >= self._window:
            raise WindowReached(
                f"{self._name}'s prompt ({prompt} tokens) and answer ({output} tokens) "
                f"together reach the {self._window}-token window, so either may be cut "
                f"short; discarded."
            )
        if reason != "stop":
            raise PossiblyTruncated(
                f"{self._name} stopped for {reason!r}, not 'stop', so the answer may be "
                f"cut short; discarded."
            )
        message = body.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str):
            raise LocalModelUnavailable(
                f"{self._name}'s answer carries no message content; refused rather "
                f"than read as an empty answer."
            )
        model = body.get("model")

        def _ns(key: str) -> int | None:
            value = body.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        return ModelAnswer(
            text=text,
            model=model if isinstance(model, str) and model else self._name,
            local=True,
            prompt_tokens=prompt,
            output_tokens=output,
            total_ns=_ns("total_duration"),
            load_ns=_ns("load_duration"),
            prompt_ns=_ns("prompt_eval_duration"),
            output_ns=_ns("eval_duration"),
        )


class _Abandoned(Exception):
    """The model was closed by the shutdown before this request was sent."""


# ── The presence check (setup, doctor) ───────────────────────────────────────


def _warning(
    detail: str, remedy: str, *, elsewhere: bool = False, not_pulled: bool = False
) -> LocalModelPresence:
    return LocalModelPresence(
        Probe(LOCAL_MODEL_PROBE, Health.WARNING, detail, remedy),
        runs_elsewhere=elsewhere,
        not_pulled=not_pulled,
    )


def local_model_presence(
    model: str,
    *,
    window: int = DEFAULT_LOCAL_MODEL_WINDOW,
    transport: httpx.BaseTransport | None = None,
    timeout: float = PRESENCE_TIMEOUT_SECONDS,
) -> LocalModelPresence:
    """Whether `model` is pulled on the local server and runs there, as a probe.

    `OK` when no model is named — an optional model left unset is not a failure
    (Andrei, 2026-10-19), and the line says how to set one up; `OK` when the
    named model is listed and
    `/api/show` names no remote host or model and lists completion among its
    capabilities; `WARNING` otherwise — Ollama not answering, answering an
    error, late or malformed (naming what came back), the model not pulled, a
    model that cannot complete, or one that runs elsewhere — each with its
    remedy. Never raises; carries no prompt text.

    The ready line names `window`, the window the daemon's adapter uses (story
    7g), and that a changed `local_model_window` takes effect at the next
    daemon start; at 32,768 its advice says how to lower it. pm-ai does not
    read the machine's memory, so the advice is the same on every machine.
    """
    try:
        return _presence(model, window=window, transport=transport, timeout=timeout)
    except Exception as unexpected:  # noqa: BLE001 — a probe reports, never raises
        return _warning(
            f"the presence check failed unexpectedly ({type(unexpected).__name__}).",
            SERVER_LIMITS,
        )


def _presence(
    model: str, *, window: int, transport: httpx.BaseTransport | None, timeout: float
) -> LocalModelPresence:
    name = normalize_model_name(model)
    if not name:
        return unset_local_model()
    if is_cloud_tag(name):
        return _warning(_cloud_sentence(name), SERVER_LIMITS, elsewhere=True)
    backend = LoopbackBackend()
    with _client(transport if transport is not None else _default_transport(backend)) as http:
        tags = _ask(http, backend, "GET", "/api/tags", None, timeout)
        if isinstance(tags, LocalModelPresence):
            return tags
        listed = tags.get("models")
        if not isinstance(listed, list) or not all(isinstance(m, dict) for m in listed):
            return _warning(
                f"Ollama on {ADDRESS} answered /api/tags without a list of models: "
                f"{_quoted(str(tags))}",
                f"Check that {ADDRESS} is the Ollama server. {SERVER_LIMITS}",
            )
        names = {
            normalize_model_name(str(m.get("name") or m.get("model") or "")).casefold()
            for m in listed
        }
        if name.casefold() not in names:
            return _warning(
                f"{name} is not pulled on this machine's Ollama server.",
                f"{_pull(name)} {SERVER_LIMITS}",
                not_pulled=True,
            )
        show = _ask(http, backend, "POST", "/api/show", {"model": name}, timeout)
        if isinstance(show, LocalModelPresence):
            return show
        fields = _remote_fields(show)
        if fields:
            return _warning(_remote_sentence(name, fields), SERVER_LIMITS, elsewhere=True)
        capabilities = show.get("capabilities")
        if isinstance(capabilities, list) and "completion" not in capabilities:
            listed_as = ", ".join(str(c) for c in capabilities) or "nothing"
            return _warning(
                f"{name} is pulled but cannot complete text: its capabilities are "
                f"{listed_as} (an embedding-only model?), so every call to it would fail.",
                f"Name a model that lists completion, such as `qwen3:8b`. {SERVER_LIMITS}",
            )
    return LocalModelPresence(
        Probe(
            LOCAL_MODEL_PROBE,
            Health.OK,
            f"{name} is pulled and runs on this machine (Ollama on {ADDRESS}), "
            f"window {window:,} tokens; a changed local_model_window takes effect at "
            f"the next daemon start.",
            _window_advice(window),
        )
    )


def _window_advice(window: int) -> str:
    """The ready line's remedy: the server's limits, and at the larger window how to lower it."""
    if window == max(LOCAL_MODEL_WINDOWS):
        return (
            f"{SERVER_LIMITS} To lower memory use, set local_model_window = "
            f"{min(LOCAL_MODEL_WINDOWS)} in config.toml."
        )
    return SERVER_LIMITS


def _ask(
    http: httpx.Client,
    backend: LoopbackBackend,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None,
    timeout: float,
) -> dict[str, Any] | LocalModelPresence:
    watchdog = _Watchdog(backend, timeout)
    try:
        with watchdog:
            response = http.request(method, path, json=payload, timeout=_timeout(timeout))
    except httpx.HTTPError as failed:
        if watchdog.fired or (
            isinstance(failed, httpx.TimeoutException)
            and not isinstance(failed, httpx.ConnectTimeout)
        ):
            return _warning(
                f"Ollama on {ADDRESS} did not answer {path} within {timeout:g} seconds.",
                f"{START_OLLAMA} {SERVER_LIMITS}",
            )
        if isinstance(failed, (httpx.ConnectError, httpx.ConnectTimeout)):
            return _warning(
                f"nothing is listening on {ADDRESS}: Ollama is not running.",
                f"{START_OLLAMA} {SERVER_LIMITS}",
            )
        return _warning(
            f"the request to Ollama on {ADDRESS} failed ({type(failed).__name__}).",
            f"{START_OLLAMA} {SERVER_LIMITS}",
        )
    if response.status_code != 200:
        return _warning(
            f"Ollama on {ADDRESS} answered {path} with {response.status_code}: "
            f"{_error_text(response)}",
            f"Check the Ollama app is running and up to date. {SERVER_LIMITS}",
        )
    try:
        body = response.json()
    except ValueError:
        return _warning(
            f"Ollama on {ADDRESS} answered {path} with something that is not JSON: "
            f"{_quoted(response.text or 'an empty body')}",
            f"Check that {ADDRESS} is the Ollama server. {SERVER_LIMITS}",
        )
    if not isinstance(body, dict):
        return _warning(
            f"Ollama on {ADDRESS} answered {path} with {type(body).__name__}, not an "
            f"object: {_quoted(response.text)}",
            f"Check that {ADDRESS} is the Ollama server. {SERVER_LIMITS}",
        )
    return body
