"""The frontier adapter: one request to Anthropic's API, built one way (AD-1 class F, AD-16).

**Nothing can reach anywhere but Anthropic's API, by construction.** The client
is built for every call with:

- `api_key` passed explicitly (the sealed key, `pm-ai frontier key`) and
  `base_url` the constant `BASE_URL` — so neither `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, a profile nor the federation
  variables are consulted (`anthropic/_client.py:190-213`, 1.2.0: explicit
  credentials are "total", an explicit base URL wins);
- `max_retries=0` (the SDK's default is 2, `_constants.py:10`);
- its own `httpx2.Client` with `trust_env=False` (no proxy, no `.netrc`, no
  setting from the environment, `httpx2/_client.py:192`), `follow_redirects=
  False`, and a `DEADLINE_SECONDS` timeout.

**The SDK's own environment reads are refused, not trusted** (spine gate
2026-10-16). Passing the key and base URL does not stop SDK 1.2.0 reading
`ANTHROPIC_CUSTOM_HEADERS` (merged into every request's headers, able to
replace the key header) or `ANTHROPIC_LOG` (logs request bodies, read at
import). While either is set (`REFUSED_ENVIRONMENT`), every call — and the key
check — is refused by name before any ledger line is written. SDK 1.2.0 reads
no OpenTelemetry variable; `tests/models/test_frontier_adapter.py` pins every
environment read in the installed SDK, so a bump that adds one fails there.

**The call** (AD-16): the Tool Runner (`client.beta.messages.tool_runner`)
with no tools and `max_iterations=1`, so nothing is ever sent a second time
without the PM. `7b`'s schema goes as structured output
(`output_config.format`); no temperature, `top_p` or `top_k`, no prefill, no
server-side `fallbacks`, and `MAX_TOKENS` sized for thinking and the answer
together. *Assumed, not verified (no key at build):* that the API accepts an
empty tool list through the Tool Runner. **Measured with a real key
(2026-10-21):** `output_config.format` refuses `maxItems` with a 400, and the
structured-outputs documentation lists the string, number and array limits as
unsupported, so `7b`'s schema is sent through `frontier_schema`: those limits
are left out and a list of types becomes `anyOf`. Nothing is lost — `7b`'s
validator checks every limit on the answer and never trusts the schema.

**The answer** is refused by name before its content is read: any stop reason
but `end_turn` (`FrontierStopped`), and a model other than the one asked for
(`FrontierModelMismatch`). Every refusal after sending carries the usage the
answer reported, which the router writes to the ledger.

**Too long is refused before sending**: the prompt is estimated locally at
`ESTIMATE_CHARACTERS_PER_TOKEN` against the model's window; no token-counting
request is made, since it would send the transcript.

`environ` is the set variables, read by the composition root through
`pm_ai.platform.environment` (the one place the environment is read): a
callable asked at every call, or a mapping in a test.

`transport` exists for tests (`httpx2.MockTransport`); wiring never passes one,
and `tests/conftest.py` replaces the real one so no test reaches the network.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx2

from pm_ai.domain.sanitize import Sanitized
from pm_ai.models.frontier.models import ESTIMATE_CHARACTERS_PER_TOKEN, FrontierModel
from pm_ai.ports import (
    FrontierFailed,
    FrontierKeyRefused,
    FrontierModelMismatch,
    FrontierNotSent,
    FrontierRefused,
    FrontierStopped,
)

__all__ = [
    "BASE_URL",
    "DEADLINE_SECONDS",
    "MAX_TOKENS",
    "REFUSED_ENVIRONMENT",
    "AnthropicFrontier",
    "FrontierReply",
    "refused_environment",
]

BASE_URL = "https://api.anthropic.com"
"""The only address this adapter can reach. A constant, never a setting."""

DEADLINE_SECONDS = 300.0
"""A request's timeout (AD-19's frontier pool): the wait for Anthropic."""

CONNECT_SECONDS = 10.0
"""Connecting to Anthropic: past this, the network is not answering."""

MAX_TOKENS = 16_000
"""The output cap, thinking and the answer together (the spine's API notes).

Thinking is on by default on the Claude 5 family, and `max_tokens` caps both.
`7b`'s largest valid answer is about 3,300 tokens; the rest is room for
thinking. Non-streaming, as the `claude-api` reference advises at this size.
"""

REFUSED_ENVIRONMENT = ("ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_LOG")
"""The SDK 1.2.0 environment reads an explicit argument does not override (spine gate)."""

_QUOTE_LIMIT = 200

UNSUPPORTED_SCHEMA_KEYWORDS = frozenset({
    "minLength", "maxLength", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties",
})
"""Limits structured outputs refuse (measured 2026-10-21: `maxItems` is a 400)
or does not list as supported; left out of what is sent, checked by `7b`'s
validator on the answer."""

SUPPORTED_STRING_FORMATS = frozenset({
    "date-time", "time", "date", "duration", "email", "hostname", "uri",
    "ipv4", "ipv6", "uuid",
})
"""The string formats structured outputs supports; any other is left out."""


def frontier_schema(shape: Any) -> Any:
    """`shape` as structured outputs accepts it: limits it refuses left out,
    unsupported string formats left out, and a list of types as `anyOf`.

    Pure and recursive; `shape` itself is never changed. What is left out is
    still checked — `7b`'s validator reads every limit on the answer.
    """
    if isinstance(shape, list):
        return [frontier_schema(item) for item in shape]
    if not isinstance(shape, Mapping):
        return shape
    out: dict[str, Any] = {}
    for key, value in shape.items():
        if key in UNSUPPORTED_SCHEMA_KEYWORDS:
            continue
        if key == "format" and value not in SUPPORTED_STRING_FORMATS:
            continue
        if key in ("properties", "$defs", "definitions") and isinstance(value, Mapping):
            out[key] = {name: frontier_schema(sub) for name, sub in value.items()}
        else:
            out[key] = frontier_schema(value)
    kinds = out.get("type")
    if isinstance(kinds, list):
        del out["type"]
        branches = [{"type": kind} for kind in kinds]
        if "anyOf" in out:
            out["allOf"] = [{"anyOf": branches}, {"anyOf": out.pop("anyOf")}]
        else:
            out["anyOf"] = branches
    return out


def refused_environment(environ: Mapping[str, str]) -> str | None:
    """The first SDK environment variable that refuses every frontier call, or `None`."""
    for name in REFUSED_ENVIRONMENT:
        if name in environ:
            return name
    return None


def _environment_sentence(name: str) -> str:
    return (
        f"{name} is set in the daemon's environment, and the Anthropic SDK reads "
        f"it whatever pm-ai passes ("
        + (
            "it adds headers to every request, and can replace the key's"
            if name == "ANTHROPIC_CUSTOM_HEADERS"
            else "it logs request bodies, which would carry the transcript"
        )
        + f"). Every frontier call is refused while it is set; nothing was sent "
        f"and no ledger line was written. Unset {name} and restart the daemon."
    )


@dataclass(frozen=True, slots=True)
class FrontierReply:
    """One accepted answer: its text, the model that answered, and the usage it reported."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int


def _quoted(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _QUOTE_LIMIT else f"{text[:_QUOTE_LIMIT]}…"


def _default_transport() -> httpx2.BaseTransport | None:
    """The real transport (`None`: httpx2's own). A function so the test suite can replace it.

    `tests/conftest.py` replaces it, so no test reaches Anthropic through wiring.
    """
    return None


class _Deadline(httpx2.BaseTransport):
    """One call's transport under a total deadline, measured from the send (story 7c).

    httpx2's timeout is per phase, so a response that trickles bytes can run
    past it in total. Entered (`with deadline:`) when the request is sent, a
    watchdog timer marks the deadline passed and closes the inner transport,
    which shuts the connection a blocked read is waiting on — as the local
    adapter's watchdog does (`pm_ai.models.local.ollama`). Every body chunk and
    every request also checks the clock, so a stream that keeps yielding is
    ended at the deadline too, as a read timeout. The transport is the call's
    own, so the adapter stays usable for the next call (unlike the shutdown).
    """

    def __init__(
        self,
        inner: httpx2.BaseTransport,
        seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._seconds = seconds
        self._clock = clock
        self._started: float | None = None
        self._timer: threading.Timer | None = None
        self.fired = False

    def __enter__(self) -> _Deadline:
        self._started = self._clock()
        self._timer = threading.Timer(self._seconds, self._fire)
        self._timer.daemon = True
        self._timer.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._timer is not None:
            self._timer.cancel()

    def _fire(self) -> None:
        self.fired = True
        try:
            self._inner.close()
        except Exception:  # noqa: BLE001, S110 — the deadline still ends the call
            pass

    def passed(self) -> bool:
        if self.fired:
            return True
        if self._started is not None and self._clock() - self._started >= self._seconds:
            self.fired = True
        return self.fired

    def _timeout(self, request: httpx2.Request) -> httpx2.ReadTimeout:
        return httpx2.ReadTimeout(
            f"the call's {self._seconds:g}-second total deadline passed", request=request
        )

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        if self.passed():
            raise self._timeout(request)
        response = self._inner.handle_request(request)
        response.stream = _DeadlineStream(response.stream, self, request)  # type: ignore[arg-type]
        return response

    def close(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._inner.close()


class _DeadlineStream(httpx2.SyncByteStream):
    """A response body that ends, as a read timeout, once the call's deadline passes."""

    def __init__(self, inner: Any, deadline: _Deadline, request: httpx2.Request) -> None:
        self._inner = inner
        self._deadline = deadline
        self._request = request

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._inner:
            if self._deadline.passed():
                raise self._deadline._timeout(self._request)  # noqa: SLF001
            yield chunk

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()


def _usage(message: Any) -> tuple[int | None, int | None]:
    usage = getattr(message, "usage", None)

    def count(name: str) -> int | None:
        value = getattr(usage, name, None)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    return count("input_tokens"), count("output_tokens")


class AnthropicFrontier:
    """The frontier side behind the router: one request per call, never retried.

    Constructing it opens nothing. `close` — the frontier pool's shutdown —
    closes a client in flight and refuses every later call.
    """

    def __init__(
        self,
        *,
        transport: httpx2.BaseTransport | None = None,
        environ: Mapping[str, str] | Callable[[], Mapping[str, str]] | None = None,
        deadline_seconds: float = DEADLINE_SECONDS,
    ) -> None:
        if deadline_seconds <= 0:
            raise ValueError(f"a deadline is a positive number of seconds, not {deadline_seconds!r}.")
        self._transport = transport
        self._environ = environ
        self._deadline = float(deadline_seconds)
        self._lock = threading.Lock()
        self._open: set[httpx2.Client] = set()
        self._closed = False

    @property
    def deadline_seconds(self) -> float:
        return self._deadline

    def close(self) -> None:
        """Close any client in flight and refuse every later call."""
        with self._lock:
            self._closed = True
            clients = list(self._open)
        for client in clients:
            try:
                client.close()
            except Exception:  # noqa: BLE001, S110 — the stop sequence goes on
                pass

    def refused(self) -> str | None:
        """Why every call is refused before anything is sent, or `None`."""
        found = self._environ
        variables = {} if found is None else found() if callable(found) else found
        name = refused_environment(variables)
        return None if name is None else _environment_sentence(name)

    # ── the client ───────────────────────────────────────────────────────────

    def _client(self, key: str) -> tuple[anthropic.Anthropic, httpx2.Client]:
        client, http, _ = self._deadlined(key)
        return client, http

    def _deadlined(self, key: str) -> tuple[anthropic.Anthropic, httpx2.Client, _Deadline]:
        """A client for one call, its transport under the call's total deadline."""
        with self._lock:
            if self._closed:
                raise FrontierNotSent(
                    "the daemon is stopping, so the frontier call was not sent and "
                    "no ledger line was written."
                )
            inner = self._transport if self._transport is not None else _default_transport()
            if inner is None:
                # httpx2's own transport, built as the client would build it with
                # `trust_env=False`, so the deadline can close its connections.
                inner = httpx2.HTTPTransport(trust_env=False)
            deadline = _Deadline(inner, self._deadline)
            http = httpx2.Client(
                trust_env=False,
                follow_redirects=False,
                timeout=httpx2.Timeout(self._deadline, connect=min(CONNECT_SECONDS, self._deadline)),
                transport=deadline,
            )
            self._open.add(http)
        client = anthropic.Anthropic(
            api_key=key,
            base_url=BASE_URL,
            max_retries=0,
            timeout=httpx2.Timeout(self._deadline, connect=min(CONNECT_SECONDS, self._deadline)),
            http_client=http,
        )
        return client, http, deadline

    def _late(self, model_id: str) -> FrontierFailed:
        return FrontierFailed(
            f"{model_id} did not answer within {self._deadline:g} seconds, the call's "
            f"total deadline; its connection was closed and the request was not retried.",
            sent=True,
        )

    def _release(self, http: httpx2.Client) -> None:
        with self._lock:
            self._open.discard(http)
        http.close()

    # ── the call ─────────────────────────────────────────────────────────────

    def complete(
        self,
        *,
        model: FrontierModel,
        key: str,
        instructions: str,
        external: Sequence[Sanitized],
        shape: Mapping[str, object] | None,
        before_send: Callable[[], None],
    ) -> FrontierReply:
        """One request to `model`, refused by name before it leaves when it may not.

        In order, each before any request: the SDK environment variables, the
        prompt's estimate against the window, then `before_send` — the router's
        `sent` ledger line; if it raises, nothing is sent. Only
        `fragment.for_model` is put in the prompt. After sending, every refusal
        carries the usage the answer reported.
        """
        refused = self.refused()
        if refused is not None:
            raise FrontierNotSent(refused)
        if MAX_TOKENS > model.max_output_tokens:
            raise FrontierNotSent(
                f"the {MAX_TOKENS}-token output cap is past {model.model_id}'s "
                f"{model.max_output_tokens}-token maximum in the price table; nothing "
                f"was sent and no ledger line was written."
            )
        user = [{"type": "text", "text": fragment.for_model} for fragment in external]
        characters = len(instructions) + sum(len(block["text"]) for block in user)
        if shape is not None:
            characters += len(json.dumps(dict(shape), ensure_ascii=False))
        estimate = math.ceil(characters / ESTIMATE_CHARACTERS_PER_TOKEN)
        if estimate + MAX_TOKENS > model.context_window:
            raise FrontierNotSent(
                f"the prompt is estimated at {estimate} tokens ({characters} characters "
                f"at {ESTIMATE_CHARACTERS_PER_TOKEN} per token), and with the "
                f"{MAX_TOKENS}-token answer that is past {model.model_id}'s "
                f"{model.context_window}-token window; nothing was sent and no ledger "
                f"line was written. It is not cut into parts."
            )
        if not user:
            raise FrontierNotSent("the call carries no text, so nothing was sent.")
        client, http, deadline = self._deadlined(key)
        try:
            try:
                before_send()
            except FrontierRefused:
                raise
            except Exception as unwritten:  # noqa: BLE001 — named, and nothing is sent
                raise FrontierNotSent(
                    f"the disclosure ledger's `sent` line could not be written "
                    f"({type(unwritten).__name__}), so nothing was sent: a request "
                    f"leaves only once its line is written."
                ) from unwritten
            options: dict[str, Any] = {}
            if shape is not None:
                options["output_config"] = {
                    "format": {"type": "json_schema", "schema": frontier_schema(shape)}
                }
            try:
                runner = client.beta.messages.tool_runner(
                    model=model.model_id,
                    max_tokens=MAX_TOKENS,
                    system=instructions,
                    messages=[{"role": "user", "content": user}],  # type: ignore[typeddict-item]
                    tools=[],
                    max_iterations=1,
                    **options,
                )
                with deadline:  # measured from the send
                    message = runner.until_done()
            except anthropic.APITimeoutError as late:
                raise self._late(model.model_id) from late
            except anthropic.APIConnectionError as dropped:
                if deadline.fired:
                    raise self._late(model.model_id) from dropped
                raise FrontierFailed(
                    f"the connection to {BASE_URL} failed before an answer arrived "
                    f"({type(dropped.__cause__ or dropped).__name__}); not retried.",
                    sent=True,
                ) from dropped
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as rejected:
                raise FrontierFailed(
                    f"Anthropic refused the held key ({rejected.status_code}), so no "
                    f"summary was made; enter a working one with `pm-ai frontier key` "
                    f"while the daemon runs. Not retried.",
                    sent=True,
                ) from rejected
            except anthropic.APIStatusError as error:
                raise FrontierFailed(
                    f"Anthropic answered {error.status_code} "
                    f"({_quoted(str(getattr(error, 'message', '') or type(error).__name__))}); "
                    f"not retried.",
                    sent=True,
                ) from error
            except FrontierRefused:
                raise
            except Exception as unexpected:  # noqa: BLE001 — after sending: named, not retried
                if deadline.fired:
                    raise self._late(model.model_id) from unexpected
                raise FrontierFailed(
                    f"the frontier call stopped on {type(unexpected).__name__} after the "
                    f"request was sent; not retried.",
                    sent=True,
                ) from unexpected
        finally:
            self._release(http)
        return _accepted(message, model)

    # ── the key check ────────────────────────────────────────────────────────

    def check_key(self, key: str) -> None:
        """Ask Anthropic to list models with `key`: no prompt, no ledger line, nothing billed.

        `FrontierKeyRefused` naming why when the key is refused, Anthropic
        cannot be asked, or an SDK environment variable is set. *Assumed:* that
        listing models bills nothing.
        """
        refused = self.refused()
        if refused is not None:
            raise FrontierKeyRefused(refused.replace("Every frontier call is", "The key check is"))
        try:
            client, http, deadline = self._deadlined(key)
        except FrontierNotSent as stopping:
            raise FrontierKeyRefused(str(stopping)) from stopping
        try:
            with deadline:
                client.models.list(limit=1)
        except anthropic.APITimeoutError as late:
            raise FrontierKeyRefused(
                f"Anthropic did not answer the key check within {self._deadline:g} seconds, "
                f"so the key was not sealed; a key already held stays."
            ) from late
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as rejected:
            raise FrontierKeyRefused(
                f"Anthropic refused the key ({rejected.status_code}), so it was not "
                f"sealed; a key already held stays."
            ) from rejected
        except anthropic.APIStatusError as error:
            raise FrontierKeyRefused(
                f"Anthropic answered the key check with {error.status_code}, so the key "
                f"was not sealed; a key already held stays."
            ) from error
        except anthropic.APIConnectionError as unreachable:
            if deadline.fired:
                raise FrontierKeyRefused(
                    f"Anthropic did not answer the key check within {self._deadline:g} "
                    f"seconds, so the key was not sealed; a key already held stays."
                ) from unreachable
            raise FrontierKeyRefused(
                f"{BASE_URL} could not be reached to check the key, so it was not "
                f"sealed; a key already held stays."
            ) from unreachable
        finally:
            self._release(http)


_FALLBACK_STOP = "no stop reason"


def _accepted(message: Any, model: FrontierModel) -> FrontierReply:
    """The answer, or the refusal naming why its content is not read."""
    input_tokens, output_tokens = _usage(message)
    answered = getattr(message, "model", None)
    answered = answered if isinstance(answered, str) else None
    stop = getattr(message, "stop_reason", None)
    if stop != "end_turn":
        named = stop if isinstance(stop, str) and stop else _FALLBACK_STOP
        raise FrontierStopped(
            f"Claude's answer stopped for {named!r}, not 'end_turn', so its content "
            f"was not read; it was not asked again.",
            stop_reason=named,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=answered,
        )
    if answered != model.model_id:
        raise FrontierModelMismatch(
            f"the answer names the model {answered!r}, not {model.model_id}, which was "
            f"asked for, so it was refused: an answer from a model the PM did not ask "
            f"for is not used.",
            sent=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=answered,
        )
    if input_tokens is None or output_tokens is None:
        raise FrontierFailed(
            f"{model.model_id}'s answer reported no usable token counts, so its cost "
            f"cannot be computed; refused rather than recorded as free.",
            sent=True,
            model=answered,
        )
    texts = [
        block.text
        for block in getattr(message, "content", None) or ()
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
    ]
    if not texts:
        raise FrontierFailed(
            f"{model.model_id}'s answer carries no text, so there is nothing to read.",
            sent=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=answered,
        )
    return FrontierReply("".join(texts), model.model_id, input_tokens, output_tokens)
