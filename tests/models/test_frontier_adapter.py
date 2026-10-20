"""Story 7c — the frontier adapter: one client, built one way, never retried.

Every test answers through an `httpx2.MockTransport`; nothing reaches
`api.anthropic.com`, and no key here is real.
"""

from __future__ import annotations

import ast
import json
import typing
from pathlib import Path

import anthropic
import httpx2
import pytest

from pm_ai.core.meeting_summary import SUMMARY_SHAPE
from pm_ai.domain.sanitize import sanitize
from pm_ai.models.frontier import anthropic as frontier
from pm_ai.models.frontier.anthropic import (
    BASE_URL,
    MAX_TOKENS,
    REFUSED_ENVIRONMENT,
    AnthropicFrontier,
)
from pm_ai.models.frontier.models import MODELS, SONNET_TIER, FrontierModel
from pm_ai.ports import (
    FrontierFailed,
    FrontierKeyRefused,
    FrontierModelMismatch,
    FrontierNotSent,
    FrontierStopped,
)

KEY = "test-key-not-real"
SONNET = MODELS[SONNET_TIER]


def _message(*, model=SONNET_TIER, stop="end_turn", text='{"summary": "ok"}', usage=(1200, 300)):
    body: dict[str, object] = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}] if text is not None else [],
        "stop_reason": stop,
        "stop_sequence": None,
    }
    if usage is not None:
        body["usage"] = {"input_tokens": usage[0], "output_tokens": usage[1]}
    return body


class Recorder:
    """A mock transport that records every request and answers from a list."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else _message()
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, httpx2.Response):
            return answer
        return httpx2.Response(200, json=answer)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self)


def _adapter(recorder: Recorder, *, environ=None) -> AnthropicFrontier:
    return AnthropicFrontier(transport=recorder.transport(), environ=environ or {})


def _call(adapter, *, model: FrontierModel = SONNET, sent: list | None = None, text="Ana: hello"):
    def before_send():
        if sent is not None:
            sent.append(True)

    return adapter.complete(
        model=model,
        key=KEY,
        instructions="pm-ai's own instructions",
        external=(sanitize("Meeting title: Sync"), sanitize(text)),
        shape=SUMMARY_SHAPE,
        before_send=before_send,
    )


# ── The request: where, how, and what it carries ─────────────────────────────


def test_a_valid_answer_returns_text_model_and_reported_usage():
    recorder = Recorder(_message())
    sent: list = []
    reply = _call(_adapter(recorder), sent=sent)
    assert sent == [True] and len(recorder.requests) == 1
    assert (reply.model, reply.input_tokens, reply.output_tokens) == (SONNET_TIER, 1200, 300)
    assert reply.text == '{"summary": "ok"}'


def test_the_environment_row_sends_to_anthropic_with_the_sealed_key_and_no_proxy(monkeypatch):
    """`ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY` and `HTTPS_PROXY` set: ignored."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://elsewhere.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-environment-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    recorder = Recorder(_message())
    _call(AnthropicFrontier(transport=recorder.transport()))
    (request,) = recorder.requests
    assert str(request.url).startswith(f"{BASE_URL}/v1/messages")
    assert request.headers["x-api-key"] == KEY
    assert "authorization" not in request.headers


def test_the_client_is_built_explicitly_without_retries_environment_or_redirects():
    adapter = AnthropicFrontier(transport=Recorder().transport(), environ={})
    client, http = adapter._client(KEY)  # noqa: SLF001 — the construction is the contract
    try:
        assert client.max_retries == 0
        assert str(client.base_url).rstrip("/") == BASE_URL
        assert client.api_key == KEY and client.auth_token is None
        assert http._trust_env is False  # noqa: SLF001
        assert http.follow_redirects is False
    finally:
        adapter._release(http)  # noqa: SLF001


def test_the_body_carries_no_fallbacks_no_sampling_no_prefill_and_the_schema():
    recorder = Recorder(_message())
    _call(_adapter(recorder))
    body = json.loads(recorder.requests[0].content)
    assert "fallbacks" not in body
    for absent in ("temperature", "top_p", "top_k"):
        assert absent not in body
    assert [m["role"] for m in body["messages"]] == ["user"]  # no assistant prefill
    assert body["model"] == SONNET_TIER and body["max_tokens"] == MAX_TOKENS
    assert body.get("tools", []) == []
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": dict(SUMMARY_SHAPE)}
    assert body["system"] == "pm-ai's own instructions"
    texts = [block["text"] for block in body["messages"][0]["content"]]
    assert texts == ["Meeting title: Sync", "Ana: hello"]


def test_one_request_per_call_even_when_the_api_errors():
    """`max_retries=0`: a 500 is refused, never asked again."""
    recorder = Recorder(httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}}))
    with pytest.raises(FrontierFailed) as caught:
        _call(_adapter(recorder))
    assert len(recorder.requests) == 1 and caught.value.sent
    assert "500" in str(caught.value)


def test_a_dropped_connection_after_sending_fails_with_input_unknown():
    recorder = Recorder(httpx2.ReadError("the connection dropped mid-answer"))
    with pytest.raises(FrontierFailed) as caught:
        _call(_adapter(recorder))
    assert caught.value.sent and not caught.value.reports_usage
    assert len(recorder.requests) == 1


# ── Refused before anything is sent ──────────────────────────────────────────


@pytest.mark.parametrize("variable", REFUSED_ENVIRONMENT)
def test_an_sdk_environment_variable_refuses_every_call_by_name(variable):
    recorder = Recorder(_message())
    sent: list = []
    with pytest.raises(FrontierNotSent) as caught:
        _call(_adapter(recorder, environ={variable: "x"}), sent=sent)
    assert variable in str(caught.value)
    assert sent == [] and recorder.requests == []


def test_the_pinned_list_is_every_sdk_read_an_explicit_argument_does_not_override():
    assert REFUSED_ENVIRONMENT == ("ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_LOG")


# Every environment read in the installed SDK (1.2.0), by file, and why each is
# safe on this adapter's path. A bump that adds a read fails here and is judged.
# Reached on this path and overridden by an explicit argument: _client.py's
# API_KEY, AUTH_TOKEN (explicit credentials are total) and BASE_URL; refused:
# CUSTOM_HEADERS and LOG; harmless: WEBHOOK_SIGNING_KEY (verifies webhooks
# only) and DEFER_PYDANTIC_BUILD (a build-time switch). The credential chain
# runs only without an explicit credential; the rest are other clients
# (Bedrock, Vertex, Foundry, Google Cloud, environments) pm-ai never builds.
_PINNED_READS = {
    "_client.py": ["<environ>", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_WEBHOOK_SIGNING_KEY"],
    "_models.py": ["<environ>", "DEFER_PYDANTIC_BUILD"],
    "_utils/_logs.py": ["<environ>", "ANTHROPIC_LOG"],
    "lib/aws/_credentials.py": ["<environ>", "AWS_DEFAULT_REGION", "AWS_REGION", "var"],
    "lib/bedrock/_client.py": ["<environ>", "ANTHROPIC_BEDROCK_BASE_URL", "AWS_BEARER_TOKEN_BEDROCK", "AWS_DEFAULT_REGION", "AWS_REGION"],
    "lib/bedrock/_mantle.py": ["<environ>", "ANTHROPIC_BEDROCK_MANTLE_BASE_URL"],
    "lib/credentials/_chain.py": ["<environ>", "ENV_API_KEY", "ENV_AUTH_TOKEN", "ENV_CONFIG_DIR", "ENV_FEDERATION_RULE_ID", "ENV_IDENTITY_TOKEN", "ENV_ORGANIZATION_ID", "ENV_PROFILE", "ENV_SCOPE", "ENV_SERVICE_ACCOUNT_ID", "ENV_WORKSPACE_ID"],
    "lib/credentials/_constants.py": ["<environ>", "ANTHROPIC_CONFIG_DIR", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_IDENTITY_TOKEN", "ANTHROPIC_IDENTITY_TOKEN_FILE", "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_PROFILE", "APPDATA"],
    "lib/credentials/_providers.py": ["<environ>", "ENV_IDENTITY_TOKEN_FILE", "env_var", "self._env_var"],
    "lib/environments/_worker.py": ["<environ>", "ANTHROPIC_WORK_SECRET", "env_var"],
    "lib/foundry.py": ["<environ>", "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_FOUNDRY_RESOURCE"],
    "lib/google_cloud/_client.py": ["<environ>", "ANTHROPIC_GOOGLE_CLOUD_BASE_URL", "ANTHROPIC_GOOGLE_CLOUD_LOCATION", "ANTHROPIC_GOOGLE_CLOUD_PROJECT", "ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID", "GOOGLE_CLOUD_PROJECT"],
    "lib/tools/agent_toolset.py": ["<environ>"],
    "lib/vertex/_client.py": ["<environ>", "ANTHROPIC_VERTEX_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION"],
}


def _environment_reads(root: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        consts: dict[str, str] = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = node.value.value
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
                names.add("<environ>")
            if isinstance(node, ast.Call) and ast.unparse(node.func) in (
                "os.environ.get", "os.getenv", "environ.get", "os.environ.pop", "os.environ.setdefault",
            ) and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant):
                    names.add(str(first.value))
                elif isinstance(first, ast.Name):
                    names.add(consts.get(first.id, first.id))
                else:
                    names.add(ast.unparse(first))
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ":
                key = node.slice
                names.add(
                    key.value
                    if isinstance(key, ast.Constant)
                    else consts.get(getattr(key, "id", ""), ast.unparse(key))
                )
        if names:
            found[str(path.relative_to(root))] = sorted(names)
    return found


def test_every_environment_read_in_the_installed_sdk_is_pinned():
    assert anthropic.__version__ == "1.2.0"
    assert _environment_reads(Path(anthropic.__file__).parent) == _PINNED_READS


def test_the_prompt_estimated_past_the_window_is_refused_naming_both_figures():
    recorder = Recorder(_message())
    tiny = FrontierModel(SONNET_TIER, 2.0, 10.0, context_window=MAX_TOKENS + 10, max_output_tokens=128_000)
    sent: list = []
    with pytest.raises(FrontierNotSent) as caught:
        _call(_adapter(recorder), model=tiny, sent=sent)
    said = str(caught.value)
    assert str(MAX_TOKENS + 10) in said and "estimated at" in said
    assert sent == [] and recorder.requests == []


def test_an_unwritable_sent_line_sends_nothing():
    recorder = Recorder(_message())

    def unwritable():
        raise OSError("disclosure.md refused the line")

    with pytest.raises(FrontierNotSent) as caught:
        _adapter(recorder).complete(
            model=SONNET, key=KEY, instructions="i", external=(sanitize("x"),),
            shape=None, before_send=unwritable,
        )
    assert "sent" in str(caught.value) and recorder.requests == []


# ── The answer: refused by name before its content is read ───────────────────


_STOPS = [s for s in typing.get_args(anthropic.types.beta.BetaStopReason) if s != "end_turn"]


def test_the_refused_stop_reasons_are_the_sdks_own_set():
    assert set(_STOPS) == {
        "max_tokens", "refusal", "pause_turn", "compaction", "tool_use",
        "stop_sequence", "model_context_window_exceeded",
    }


@pytest.mark.parametrize("stop", _STOPS)
def test_every_stop_reason_but_end_turn_is_refused_by_name_with_its_usage(stop):
    recorder = Recorder(_message(stop=stop))
    with pytest.raises(FrontierStopped) as caught:
        _call(_adapter(recorder))
    refusal = caught.value
    assert refusal.stop_reason == stop and stop in str(refusal)
    assert (refusal.input_tokens, refusal.output_tokens) == (1200, 300)
    assert len(recorder.requests) == 1, "nothing is sent a second time without the PM"


def test_an_answer_naming_another_model_is_refused_by_name():
    recorder = Recorder(_message(model="claude-opus-5"))
    with pytest.raises(FrontierModelMismatch) as caught:
        _call(_adapter(recorder))
    assert "claude-opus-5" in str(caught.value) and caught.value.model == "claude-opus-5"
    assert caught.value.reports_usage


def test_an_answer_without_usage_is_refused_rather_than_recorded_free():
    recorder = Recorder(_message(usage=None))
    with pytest.raises(FrontierFailed) as caught:
        _call(_adapter(recorder))
    assert caught.value.sent and caught.value.model == SONNET_TIER
    assert "recorded as free" in str(caught.value)


# ── The key check ────────────────────────────────────────────────────────────


def _models_page():
    return {"data": [{"id": SONNET_TIER, "type": "model", "display_name": "Sonnet", "created_at": "2026-01-01T00:00:00Z"}], "has_more": False, "first_id": SONNET_TIER, "last_id": SONNET_TIER}


def test_the_key_check_lists_models_and_carries_no_prompt():
    recorder = Recorder(_models_page())
    _adapter(recorder).check_key(KEY)
    (request,) = recorder.requests
    assert request.method == "GET" and str(request.url).startswith(f"{BASE_URL}/v1/models")
    assert request.headers["x-api-key"] == KEY and not request.content


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_named_and_never_said(status):
    recorder = Recorder(httpx2.Response(status, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
    with pytest.raises(FrontierKeyRefused) as caught:
        _adapter(recorder).check_key(KEY)
    assert "refused the key" in str(caught.value) and KEY not in str(caught.value)


def test_the_key_check_with_the_network_down_is_named():
    recorder = Recorder(httpx2.ConnectError("network down"))
    with pytest.raises(FrontierKeyRefused) as caught:
        _adapter(recorder).check_key(KEY)
    assert "could not be reached" in str(caught.value)


def test_the_key_check_is_refused_while_an_sdk_variable_is_set():
    recorder = Recorder(_models_page())
    with pytest.raises(FrontierKeyRefused):
        _adapter(recorder, environ={"ANTHROPIC_CUSTOM_HEADERS": "x-api-key: other"}).check_key(KEY)
    assert recorder.requests == []


def test_the_suites_default_transport_never_reaches_anthropic():
    """`tests/conftest.py` replaces the real transport: a wired adapter meets a refusal."""
    with pytest.raises(FrontierFailed):
        _call(AnthropicFrontier(environ={}))
    assert frontier._default_transport() is not None


def test_a_closed_adapter_sends_nothing():
    recorder = Recorder(_message())
    adapter = _adapter(recorder)
    adapter.close()
    with pytest.raises(FrontierNotSent):
        _call(adapter)
    assert recorder.requests == []
