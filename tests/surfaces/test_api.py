"""The loopback API's matrix rows that need no socket — story 4e (AD-8).

Driven through Starlette's test client against `create_app`, which is what the
daemon serves. The lifecycle rows — binding, the token file, signals — need a
real process and live in `tests/slice/test_daemon_slice.py`.
"""

from __future__ import annotations

import json
import logging

import pytest
from starlette.testclient import TestClient

from pm_ai.platform import doctor
from pm_ai.surfaces.api import app as api
from pm_ai.surfaces.api.app import (
    IDENTITY_HEADER,
    REQUEST_LOGGER,
    STATUS_PATH,
    TOKEN_REQUIRED,
    create_app,
)

TOKEN = "t" * 43
STATUS = {
    "probes": [{"name": "git", "state": "ok", "detail": "answers"}],
    "healthy": True,
    "uptime_seconds": 7,
    "started_at": "2026-10-12T08:00:00+00:00",
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(token=TOKEN, status=lambda: STATUS))


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def test_a_request_without_the_header_is_401_and_says_only_that(client):
    response = client.get(STATUS_PATH)
    assert response.status_code == 401
    assert response.json() == {"detail": TOKEN_REQUIRED}


@pytest.mark.parametrize(
    "header",
    [
        f"Bearer {'u' * 43}",
        f"Bearer {TOKEN[:-1]}",
        f"Bearer {TOKEN}x",
        f"Basic {TOKEN}",
        TOKEN,
        "Bearer ",
    ],
    ids=["other", "prefix", "longer", "basic", "no-scheme", "empty"],
)
def test_any_other_value_is_401_with_the_same_body(client, header):
    response = client.get(STATUS_PATH, headers={"Authorization": header})
    assert response.status_code == 401
    assert response.json() == {"detail": TOKEN_REQUIRED}
    assert TOKEN not in response.text


def test_the_valid_token_reads_the_status(client):
    response = client.get(STATUS_PATH, headers=_bearer(TOKEN))
    assert response.status_code == 200
    assert response.json() == STATUS


def test_an_unknown_path_with_the_token_is_404_and_without_it_401(client):
    assert client.get("/v1/anything?x=1", headers=_bearer(TOKEN)).status_code == 404
    # Answered before routing, so an unauthenticated caller learns nothing
    # about which paths exist.
    assert client.get("/v1/anything?x=1").status_code == 401


def test_every_response_names_itself_as_pm_ai_and_doctor_agrees_on_the_name(client):
    """`doctor` tells a pm-ai daemon from a foreign service by this header.

    `platform` may not import `surfaces`, so the probe spells the name again;
    this pins the two spellings together.
    """
    assert IDENTITY_HEADER == doctor._IDENTITY_HEADER
    assert client.get(STATUS_PATH).headers[IDENTITY_HEADER] == "1"
    assert client.get(STATUS_PATH, headers=_bearer(TOKEN)).headers[IDENTITY_HEADER] == "1"


def test_the_request_log_has_method_path_and_status_and_nothing_else(client, caplog):
    """The `?` part of the address and every header stay out of the log."""
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER)
    client.get("/v1/status?debug=1")
    client.get("/v1/anything?x=1", headers=_bearer(TOKEN))
    lines = [r.getMessage() for r in caplog.records if r.name == REQUEST_LOGGER]
    assert lines == ["GET /v1/status 401", "GET /v1/anything 404"]
    for record in caplog.records:
        rendered = f"{record.getMessage()} {record.__dict__}"
        assert "debug" not in rendered
        assert "x=1" not in rendered
        assert TOKEN not in rendered
        assert "Bearer" not in rendered


def test_the_lazily_served_app_has_no_token_and_refuses_everything():
    """What `test_ad8_*` serves: built on first access with nothing composed."""
    client = TestClient(api.app)
    assert client.get(STATUS_PATH).status_code == 401
    assert client.get(STATUS_PATH, headers=_bearer(TOKEN)).status_code == 401
    assert client.get(STATUS_PATH, headers=_bearer("")).status_code == 401


# ── Review fixes: other scope types, a failing handler, a long path ──────────


def test_an_unauthenticated_websocket_is_closed_with_policy_violation(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/status"):
            pass
    assert closed.value.code == 1008


def test_an_authenticated_websocket_reaches_no_route_and_is_not_served(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/status", headers=_bearer(TOKEN)):
            pass
    assert closed.value.code != 1008


def test_a_handler_that_raises_is_logged_as_500(caplog):
    def broken() -> dict[str, object]:
        raise RuntimeError("probe blew up")

    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER)
    client = TestClient(create_app(token=TOKEN, status=broken), raise_server_exceptions=False)
    assert client.get(STATUS_PATH, headers=_bearer(TOKEN)).status_code == 500
    records = [r for r in caplog.records if r.name == REQUEST_LOGGER]
    assert [r.getMessage() for r in records] == ["GET /v1/status 500"]
    assert records[0].status == 500


def test_a_raw_asgi_failure_before_any_answer_is_logged_as_500(caplog):
    """No framework in between: the wrapped application raises before `http.response.start`."""
    import asyncio

    from pm_ai.surfaces.api.app import AuthenticatedApp

    async def explodes(scope, receive, send):
        raise RuntimeError("before any answer")

    wrapped = AuthenticatedApp(explodes, TOKEN)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/status",
        "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
    }

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER)
    with pytest.raises(RuntimeError):
        asyncio.run(wrapped(scope, receive, send))
    records = [r for r in caplog.records if r.name == REQUEST_LOGGER]
    assert records[0].status == 500
    assert records[0].getMessage() == "GET /v1/status 500"


def test_a_long_path_is_cut_in_the_log(client, caplog):
    from pm_ai.surfaces.api.app import LOGGED_PATH_LIMIT

    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER)
    client.get("/v1/" + "a" * 5000)
    (record,) = [r for r in caplog.records if r.name == REQUEST_LOGGER]
    assert len(record.path) == LOGGED_PATH_LIMIT + 1
    assert record.path.endswith("…")
    assert set(record.__dict__) >= {"method", "path", "status"}


# ── Story 4r: the prompt, the job and its stream ─────────────────────────────

from pm_ai.surfaces.api.app import (  # noqa: E402
    JOB_EVENTS_PATH,
    JOB_PATH,
    LINE_LIMIT,
    PROMPT_PATH,
    Reply,
)

ROW = {"id": "job_0123456789abcdef", "kind": "dashboard", "scope": "personal",
       "state": "done", "result": {"output": "x\n", "exit": 0},
       "created_at": "2026-10-14T08:00:00+00:00", "updated_at": "2026-10-14T08:00:01+00:00"}


@pytest.fixture
def jobs() -> TestClient:
    lines: list[str] = []

    async def prompt(line: str) -> Reply:
        lines.append(line)
        if line == "busy":
            return Reply(503, {"detail": "busy"}, {"Retry-After": "1"})
        if line == "dashboard":
            return Reply(202, {"inline": False, "job_id": ROW["id"]})
        return Reply(200, {"inline": True, "output": "said\n", "exit": 0})

    async def job(job_id: str):
        return ROW if job_id == ROW["id"] else None

    async def job_events(job_id: str):
        if job_id != ROW["id"]:
            return None

        async def events():
            for state in ("queued", "running", "done"):
                yield {**ROW, "state": state}

        return events()

    client = TestClient(
        create_app(token=TOKEN, status=lambda: STATUS, prompt=prompt, job=job, job_events=job_events)
    )
    client.lines = lines  # type: ignore[attr-defined]
    return client


def _job(path: str, job_id: str = ROW["id"]) -> str:
    return path.replace("{job_id}", job_id)


def test_the_three_endpoints_refuse_without_the_token(jobs):
    assert jobs.post(PROMPT_PATH, json={"line": "help"}).status_code == 401
    assert jobs.get(_job(JOB_PATH)).status_code == 401
    assert jobs.get(_job(JOB_EVENTS_PATH)).status_code == 401
    assert jobs.lines == []


def test_a_line_is_answered_inline_or_with_a_job_id(jobs):
    inline = jobs.post(PROMPT_PATH, json={"line": "help"}, headers=_bearer(TOKEN))
    assert inline.status_code == 200
    assert inline.json() == {"inline": True, "output": "said\n", "exit": 0}
    slow = jobs.post(PROMPT_PATH, json={"line": "dashboard"}, headers=_bearer(TOKEN))
    assert slow.status_code == 202 and slow.json() == {"inline": False, "job_id": ROW["id"]}


def test_a_refused_row_is_503_with_retry_after(jobs):
    busy = jobs.post(PROMPT_PATH, json={"line": "busy"}, headers=_bearer(TOKEN))
    assert busy.status_code == 503
    assert busy.headers["retry-after"] == "1"
    assert "job_id" not in busy.json()


@pytest.mark.parametrize(
    "body",
    [b"x", b"{}", b'{"line": 3}', b'{"line": null}', b"[]"],
    ids=["not-json", "no-line", "number", "null", "list"],
)
def test_a_body_without_a_text_line_is_the_frameworks_422(jobs, body):
    answered = jobs.post(
        PROMPT_PATH,
        content=body,
        headers={**_bearer(TOKEN), "Content-Type": "application/json"},
    )
    assert answered.status_code == 422
    assert jobs.lines == []


@pytest.mark.parametrize("line", ["", "x" * LINE_LIMIT, "x" * (LINE_LIMIT + 10)], ids=["empty", "at-limit", "over"])
def test_a_bad_line_is_400_naming_line(jobs, line):
    answered = jobs.post(PROMPT_PATH, json={"line": line}, headers=_bearer(TOKEN))
    assert answered.status_code == 400
    assert "`line`" in answered.json()["detail"]
    assert jobs.lines == []


def test_the_longest_line_accepted_is_one_under_the_limit(jobs):
    answered = jobs.post(PROMPT_PATH, json={"line": "x" * (LINE_LIMIT - 1)}, headers=_bearer(TOKEN))
    assert answered.status_code == 200


def test_a_job_is_its_row_and_an_unknown_id_is_404_on_both(jobs):
    found = jobs.get(_job(JOB_PATH), headers=_bearer(TOKEN))
    assert found.status_code == 200 and found.json() == ROW
    assert jobs.get(_job(JOB_PATH, "job_nope"), headers=_bearer(TOKEN)).status_code == 404
    assert jobs.get(_job(JOB_EVENTS_PATH, "job_nope"), headers=_bearer(TOKEN)).status_code == 404


def test_the_stream_is_the_frameworks_event_stream_and_closes_after_the_last(jobs):
    with jobs.stream("GET", _job(JOB_EVENTS_PATH), headers=_bearer(TOKEN)) as streamed:
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")
        text = "".join(streamed.iter_text())
    frames = [frame for frame in text.split("\n\n") if frame]
    assert [json.loads(frame.removeprefix("data: "))["state"] for frame in frames] == [
        "queued", "running", "done"
    ]


def test_an_async_status_source_is_awaited():
    async def status():
        return STATUS

    client = TestClient(create_app(token=TOKEN, status=status))
    answered = client.get(STATUS_PATH, headers=_bearer(TOKEN))
    assert answered.status_code == 200 and answered.json() == STATUS
