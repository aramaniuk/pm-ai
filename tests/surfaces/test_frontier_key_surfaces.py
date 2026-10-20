"""Story 7c — `pm-ai frontier key`'s surfaces: the API endpoint and the loopback client.

No key here is real, and nothing reaches Anthropic.
"""

from __future__ import annotations

import json
import logging

from starlette.testclient import TestClient

from pm_ai.surfaces.api.app import (
    FRONTIER_KEY_PATH,
    KEY_LIMIT,
    REQUEST_LOGGER,
    Reply,
    create_app,
)
from pm_ai.surfaces.cli.loopback import Answer
from tests.surfaces.test_cli_loopback import TOKEN, _client, _json, _token, daemon  # noqa: F401 — fixture

KEY = "test-key-not-real"
API_TOKEN = "t" * 43


def _key_client(handler=None):
    async def sealed(key):
        return Reply(200, {"inline": True, "output": "sealed\n", "exit": 0})

    return TestClient(create_app(token=API_TOKEN, frontier_key=handler or sealed))


def _authorized():
    return {"Authorization": f"Bearer {API_TOKEN}"}


def test_the_endpoint_refuses_an_empty_and_an_over_long_key():
    client = _key_client()
    assert client.post(FRONTIER_KEY_PATH, json={"key": ""}, headers=_authorized()).status_code == 400
    long = client.post(FRONTIER_KEY_PATH, json={"key": "k" * KEY_LIMIT}, headers=_authorized())
    assert long.status_code == 400 and "k" * 20 not in long.text


def test_the_endpoint_answers_503_with_no_handler_wired():
    client = TestClient(create_app(token=API_TOKEN))
    answer = client.post(FRONTIER_KEY_PATH, json={"key": KEY}, headers=_authorized())
    assert answer.status_code == 503 and KEY not in answer.text


def test_the_request_log_never_records_the_key(caplog):
    caplog.set_level(logging.DEBUG)
    answer = _key_client().post(FRONTIER_KEY_PATH, json={"key": KEY}, headers=_authorized())
    assert answer.status_code == 200
    lines = [r.getMessage() for r in caplog.records if r.name == REQUEST_LOGGER]
    assert lines == [f"POST {FRONTIER_KEY_PATH} 200"]
    for record in caplog.records:
        assert KEY not in record.getMessage() and KEY not in repr(record.__dict__)


def test_the_client_posts_the_key_as_json_to_its_path_with_the_bearer_token(daemon, tmp_path):  # noqa: F811
    daemon.replies[("POST", FRONTIER_KEY_PATH)] = _json(
        200, {"inline": True, "output": "sealed\n", "exit": 0}
    )
    outcome = _client(_token(tmp_path / "t"), daemon.port).hand_frontier_key(KEY)
    assert outcome == Answer("sealed\n", 0)
    (seen,) = daemon.seen
    assert (seen.method, seen.path) == ("POST", FRONTIER_KEY_PATH)
    assert seen.authorization == f"Bearer {TOKEN}"
    assert json.loads(seen.body) == {"key": KEY}
