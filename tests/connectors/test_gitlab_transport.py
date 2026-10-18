"""Story 8n — the GitLab transport, every status reading pinned, no socket opened.

Three seams, in the order a request crosses them:

- **The opener.** `urllib.request`'s `build_opener` *and* `urlopen` are replaced
  in-process, as `test_graph_transport.py` replaces them, so the production
  transport — `_urllib_transport`, the default every composed connector uses —
  runs against the standard library's own shapes and an answer the fixture does
  not hold is an `AssertionError` rather than a DNS lookup.
- **The transport.** `GitLabClient.transport` is replaced by `Wire`, which
  records each request, so the status dispatch, the throttle handling and the
  paging header are asserted on what was sent.
- **Composition.** A hand-written `connectors/` row goes through `build()`, and
  the first recorded request's host and `since` are asserted — the settings
  composition reads are only proven by what goes out.

Nothing here sleeps: every wait is recorded.
"""

from __future__ import annotations

import email.message
import io
import json
import threading
import urllib.error
import urllib.request
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import pytest
from gitlab_fakes import TOKEN, Wire, WireOverrun, commit, page

from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import build
from pm_ai.connectors.gitlab import (
    GitLabConnectorAdapter,
    PageUnavailable,
)
from pm_ai.connectors.gitlab.client import (
    DEFAULT_THROTTLE_BACKOFF,
    MAX_BODY_BYTES,
    MAX_HEADER_DIGITS,
    PER_PAGE,
    SOCKET_TIMEOUT_SECONDS,
    TOKEN_HEADER,
    USER_AGENT,
    GitLabClient,
    GitLabRequest,
    GitLabResponse,
    _urllib_transport,
)
from pm_ai.connectors.registry import ConnectorRegistry
from pm_ai.core.connector_enrolment import enrol_connector
from pm_ai.domain.harvest import Cursor, HarvestOutcome
from pm_ai.domain.health import Health
from pm_ai.domain.identity import DataScope, ScopeKind

NOW = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)
HOST = "https://gitlab.com"
PROJECT = "acme/web"
COMMITS = f"{HOST}/api/v4/projects/acme%2Fweb/repository/commits"
URL = f"{COMMITS}?since=2026-08-12T09:00:00Z&per_page=100&page=1"
SCOPE = DataScope(ScopeKind.PROJECT, "alpha")
APPLICATION = DataScope(ScopeKind.APPLICATION)


class Clock:
    """A clock tests move by hand; `wait` advances it, so a honoured hint is
    visible as time that passed rather than as a sleep."""

    def __init__(self, at: datetime = NOW) -> None:
        self.at = at
        self.waited: list[float] = []

    def __call__(self) -> datetime:
        return self.at

    def wait(self, seconds: float) -> None:
        self.waited.append(seconds)
        self.at += timedelta(seconds=seconds)


def client(wire: Wire, **kwargs: Any) -> GitLabClient:
    kwargs.setdefault("now", lambda: NOW)
    return GitLabClient(
        host=kwargs.pop("host", HOST),
        project=kwargs.pop("project", PROJECT),
        token=kwargs.pop("token", TOKEN),
        transport=wire,
        **kwargs,
    )


def refusal(wire: Wire, **kwargs: Any) -> PageUnavailable:
    with pytest.raises(PageUnavailable) as refused:
        client(wire, **kwargs).commits(SINCE, 1)
    assert TOKEN not in refused.value.reason, "a failure sentence never carries the token"
    assert not wire.overrun
    return refused.value


def answer(status: int, headers: dict[str, str] | None = None, body: Any = None) -> GitLabResponse:
    return GitLabResponse(status=status, body=body if body is not None else {"message": "x"}, headers=headers or {})


# ── The request ──────────────────────────────────────────────────────────────


def test_one_page_is_one_get_for_the_default_branch_with_the_token_header():
    """The URL-encoded path, `since` with a `Z`, 100 a page, and no branch.

    No `ref_name` and no `all`: the list is the default branch's, and work
    reaches it by merge.
    """
    wire = Wire(page(commit("a1", at=NOW)))
    result = client(wire).commits(SINCE, 1)

    (sent,) = wire.sent
    assert sent.url == URL
    assert sent.headers[TOKEN_HEADER] == TOKEN
    query = wire.query()
    assert query["since"] == ["2026-08-12T09:00:00Z"], "never `+00:00`"
    assert query["per_page"] == [str(PER_PAGE)] == ["100"]
    assert "ref_name" not in query and "all" not in query
    assert TOKEN not in repr(sent), "a recorded request's repr names headers, not values"
    assert len(result.rows) == 1 and result.next_page is None


def test_a_host_with_a_path_is_asked_beneath_it():
    """Row: *host with a path* — a self-managed instance under `/gitlab`."""
    wire = Wire(page())
    client(wire, host="https://git.internal/gitlab").commits(SINCE, 1)

    assert wire.sent[0].url.startswith(
        "https://git.internal/gitlab/api/v4/projects/acme%2Fweb/repository/commits?"
    )


def test_the_next_page_header_is_followed_and_a_blank_one_is_the_last_page():
    paged = client(Wire(page(commit("a1", at=NOW), next_page=2))).commits(SINCE, 1)
    last = client(Wire(page(commit("a2", at=NOW), next_page=None))).commits(SINCE, 2)
    absent = client(Wire(GitLabResponse(200, body=[], headers={}))).commits(SINCE, 1)

    assert (paged.next_page, last.next_page, absent.next_page) == (2, None, None)


# ── What each answer means ───────────────────────────────────────────────────


def test_401_is_the_token_refused_and_not_retryable():
    refused = refusal(Wire(answer(401)))
    assert refused.status == 401 and refused.retryable is False
    assert "refused the token" in refused.reason


def test_403_names_the_read_api_scope():
    refused = refusal(Wire(answer(403)))
    assert refused.retryable is False
    assert "read_api" in refused.reason


def test_404_names_the_path_and_the_host():
    """Row: *project not visible* — GitLab answers 404 for a project the token
    cannot see, so the sentence says that rather than "does not exist"."""
    refused = refusal(Wire(answer(404)))
    assert refused.retryable is False
    assert PROJECT in refused.reason and HOST in refused.reason
    assert "cannot see" in refused.reason


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_server_error_is_retryable(status):
    refused = refusal(Wire(answer(status)))
    assert refused.retryable is True and str(status) in refused.reason


@pytest.mark.parametrize("status", [400, 418, 304])
def test_any_other_status_is_named_and_not_retryable(status):
    refused = refusal(Wire(answer(status)))
    assert refused.retryable is False and str(status) in refused.reason


def test_a_failure_sentence_is_never_the_response_body():
    """Reasons are composed from the status; a body is provider text."""
    body = {"message": "401 Unauthorized — token glpat-echoed-back"}
    refused = refusal(Wire(GitLabResponse(401, body=body)))
    assert "echoed" not in refused.reason


@pytest.mark.parametrize("body", [{}, None, "a string", 7])
def test_a_200_whose_body_is_not_a_list_is_refused(body):
    """Row: *body not a list* — not read as an empty page."""
    refused = refusal(Wire(GitLabResponse(200, body=body)))
    assert refused.retryable is False
    assert "not a GitLab commit list" in refused.reason


def test_an_unreadable_next_page_header_is_refused():
    """Row: *next page unreadable* — `abc` says nothing about where the list
    continues."""
    refused = refusal(Wire(page(commit("a1", at=NOW), next_page="abc")))
    assert refused.retryable is False
    assert "next-page" in refused.reason


# ── Throttling ───────────────────────────────────────────────────────────────


def test_a_throttle_hint_that_fits_is_waited_and_the_page_re_asked():
    """Row: *throttled, fits* — five seconds, through the injected wait."""
    clock = Clock()
    wire = Wire(answer(429, {"Retry-After": "5"}), page(commit("a1", at=NOW)))
    result = client(wire, now=clock, wait=clock.wait).commits(SINCE, 1)

    assert clock.waited == [5.0]
    assert len(wire.sent) == 2 and wire.sent[0].url == wire.sent[1].url
    assert len(result.rows) == 1


@pytest.mark.parametrize(
    "headers",
    [{}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, {"Retry-After": "soon"}],
    ids=["no-hint", "a-date", "junk"],
)
def test_a_throttle_with_no_seconds_waits_the_stated_default(headers):
    """Row: *throttled, no hint or a date* — thirty seconds within the budget."""
    clock = Clock()
    wire = Wire(answer(429, headers), page())
    client(wire, now=clock, wait=clock.wait).commits(SINCE, 1)

    assert clock.waited == [DEFAULT_THROTTLE_BACKOFF.total_seconds()] == [30.0]


def test_a_hint_past_the_budget_ends_the_walk_with_the_hint():
    """Row: *throttled, does not fit* — retryable, hint on the failure, no wait."""
    clock = Clock()
    deadline = NOW + timedelta(seconds=60)
    wire = Wire(answer(429, {"Retry-After": "3600"}))
    with pytest.raises(PageUnavailable) as refused:
        client(wire, now=clock, wait=clock.wait).commits(SINCE, 2, deadline=deadline)

    assert clock.waited == []
    assert refused.value.retryable is True
    assert refused.value.retry_after == timedelta(hours=1)


def test_a_default_wait_that_does_not_fit_ends_the_walk_too():
    clock = Clock()
    wire = Wire(answer(429))
    with pytest.raises(PageUnavailable) as refused:
        client(wire, now=clock, wait=clock.wait).commits(
            SINCE, 2, deadline=NOW + timedelta(seconds=10)
        )
    assert refused.value.retry_after == DEFAULT_THROTTLE_BACKOFF
    assert clock.waited == []


def test_a_third_throttle_on_one_page_ends_the_walk():
    clock = Clock()
    wire = Wire(*(answer(429, {"Retry-After": "1"}) for _ in range(3)))
    with pytest.raises(PageUnavailable) as refused:
        client(wire, now=clock, wait=clock.wait).commits(SINCE, 1)

    assert clock.waited == [1.0, 1.0]
    assert len(wire.sent) == 3
    assert refused.value.retryable is True
    assert refused.value.retry_after == timedelta(seconds=1)


def test_a_client_composed_with_no_wait_refuses_rather_than_spinning():
    """Row: *no wait injected* — the hint travels with the failure."""
    wire = Wire(answer(429, {"Retry-After": "5"}))
    with pytest.raises(PageUnavailable) as refused:
        client(wire).commits(SINCE, 1)

    assert len(wire.sent) == 1, "re-asked a rate-limited provider immediately"
    assert refused.value.retryable is True
    assert refused.value.retry_after == timedelta(seconds=5)
    assert "no way to wait" in refused.value.reason


# ── What is refused before any socket ────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "http://gitlab.com",
        "https://gitlab.com/?x",
        "https://gitlab.com/",
        "https://gitlab.com#top",
        "https://me@gitlab.com",
        "https:///nohost",
        "",
    ],
)
def test_a_bad_host_is_refused_before_any_socket(host):
    """Row: *bad host* — not retryable, nothing sent."""
    wire = Wire()
    refused = refusal(wire, host=host)
    assert wire.sent == []
    assert refused.retryable is False


@pytest.mark.parametrize("project", ["", "   ", "/acme/web", "acme/web?x", "acme#web", "acme web"])
def test_a_project_path_that_would_change_the_request_is_refused(project):
    wire = Wire()
    refusal(wire, project=project)
    assert wire.sent == []


@pytest.mark.parametrize(
    "token, cause",
    [(f"{TOKEN}\nX-Injected: 1", "line break"), ("glpat-ünïcode", "non-ASCII"), ("", "no token")],
)
def test_a_token_that_cannot_be_a_header_value_opens_no_socket(token, cause):
    """Row: *bad token value* — the cause is named, the token is not."""
    wire = Wire()
    with pytest.raises(PageUnavailable) as refused:
        client(wire, token=token).commits(SINCE, 1)

    assert wire.sent == []
    assert refused.value.retryable is False
    assert cause in refused.value.reason
    assert "glpat" not in refused.value.reason


def test_an_unclassified_fault_out_of_an_injected_transport_is_named_by_type():
    wire = Wire(RuntimeError(f"broke while sending {TOKEN}"))
    refused = refusal(wire)
    assert "RuntimeError" in refused.reason
    assert refused.retryable is False


# ── The production transport, against a replaced opener ─────────────────────


def _headers(pairs: dict[str, str]) -> email.message.Message:
    message = email.message.Message()
    for name, value in pairs.items():
        message[name] = value
    return message


class Answer:
    """What `urllib` yields for a status it does not consider an error."""

    def __init__(
        self,
        status: int = 200,
        body: bytes = b"[]",
        headers: dict[str, str] | None = None,
        url: str = URL,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = _headers(headers or {"Content-Type": "application/json"})
        self.url = url

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def __enter__(self) -> Answer:
        return self

    def __exit__(self, *_: Any) -> bool:
        return False


def _error(status: int, body: bytes = b"{}", headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, status, f"status {status}", _headers(headers or {}), io.BytesIO(body))


class Sockets:
    """Stands in for everything in `urllib.request` that could open one."""

    def __init__(self, *answers: Answer | Exception) -> None:
        self.answers: list[Answer | Exception] = list(answers)
        self.handlers: list[Any] = []
        self.opened: list[urllib.request.Request] = []
        self.timeouts: list[float | None] = []
        self.urlopened = 0

    def build_opener(self, *handlers: Any) -> Sockets:
        self.handlers.extend(handlers)
        return self

    def urlopen(self, request: Any, timeout: float | None = None) -> Answer:
        self.urlopened += 1
        return self.open(request, timeout=timeout)

    def open(self, request: Any, timeout: float | None = None) -> Answer:
        self.opened.append(request)
        self.timeouts.append(timeout)
        if not self.answers:
            raise AssertionError(f"an unexpected request: {request.full_url!r}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _no_sockets(monkeypatch: pytest.MonkeyPatch, *answers: Answer | Exception) -> Sockets:
    sockets = Sockets(*answers)
    monkeypatch.setattr(urllib.request, "build_opener", sockets.build_opener)
    monkeypatch.setattr(urllib.request, "urlopen", sockets.urlopen)
    return sockets


def _request(url: str = URL) -> GitLabRequest:
    return GitLabRequest(url=url, headers={TOKEN_HEADER: TOKEN, "Accept": "application/json"})


def test_the_transport_exercised_here_is_the_default_everywhere():
    for owner in (GitLabClient, GitLabConnectorAdapter):
        (transport,) = [field for field in fields(owner) if field.name == "transport"]
        assert transport.default is _urllib_transport


def test_a_200_arrives_parsed_with_its_headers_and_the_thirty_second_timeout(monkeypatch):
    body = json.dumps([{"id": "a1"}]).encode()
    sockets = _no_sockets(monkeypatch, Answer(body=body, headers={"X-Next-Page": "2"}))
    answered = _urllib_transport(_request())

    assert answered.status == 200
    assert answered.body == [{"id": "a1"}]
    assert answered.header("x-next-page") == "2"
    assert sockets.opened[0].get_method() == "GET"
    assert sockets.opened[0].get_header("Private-token") == TOKEN
    assert sockets.timeouts == [SOCKET_TIMEOUT_SECONDS] == [30]
    assert sockets.urlopened == 0, "urlopen's default opener follows redirects"


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_every_error_status_arrives_as_exactly_that_status(status, monkeypatch):
    _no_sockets(monkeypatch, _error(status, headers={"Retry-After": "5"}))
    answered = _urllib_transport(_request())
    assert answered.status == status
    assert answered.header("Retry-After") == "5"


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_is_refused_not_followed(status, monkeypatch):
    """Row: *redirect* — one request, and the `Location` never asked."""
    sockets = _no_sockets(monkeypatch, _error(status, headers={"Location": "https://evil.invalid/api"}))
    with pytest.raises(PageUnavailable) as refused:
        _urllib_transport(_request())

    assert refused.value.retryable is False
    assert "redirect" in refused.value.reason and str(status) in refused.value.reason
    assert [request.full_url for request in sockets.opened] == [URL]
    for handler in sockets.handlers:
        if isinstance(handler, urllib.request.HTTPRedirectHandler):
            assert handler.redirect_request(
                urllib.request.Request(URL, headers={TOKEN_HEADER: TOKEN}),
                io.BytesIO(b""), status, "Moved", _headers({}), "https://evil.invalid/api",
            ) is None


def test_an_answer_from_another_origin_is_refused(monkeypatch):
    _no_sockets(monkeypatch, Answer(url="https://gitlab.com.evil.invalid/api"))
    with pytest.raises(PageUnavailable) as refused:
        _urllib_transport(_request())
    assert refused.value.retryable is False and "different origin" in refused.value.reason


@pytest.mark.parametrize(
    "silence",
    [ConnectionRefusedError(f"refused {URL}"), TimeoutError(f"timed out on {URL}"), OSError("reset")],
    ids=["refused", "silent-30s", "reset"],
)
def test_an_unreachable_host_is_retryable_and_named_by_type_only(silence, monkeypatch):
    """Row: *host unreachable* — the exception type, never its text."""
    _no_sockets(monkeypatch, silence)
    with pytest.raises(PageUnavailable) as refused:
        _urllib_transport(_request())

    assert refused.value.retryable is True
    assert type(silence).__name__ in refused.value.reason
    assert "since=" not in refused.value.reason and "refused http" not in refused.value.reason


def test_a_url_that_is_not_https_never_reaches_a_socket(monkeypatch):
    sockets = _no_sockets(monkeypatch, Answer())
    with pytest.raises(PageUnavailable):
        _urllib_transport(_request("http://gitlab.com/api/v4/projects/x/repository/commits"))
    assert sockets.opened == []


def test_the_client_reads_a_real_transport_answer_end_to_end(monkeypatch):
    """The default transport inside the client: a 429 with a hint waited, then
    a page with a next-page header — through the replaced opener."""
    body = json.dumps([commit("a1", at=NOW)]).encode()
    _no_sockets(
        monkeypatch,
        _error(429, headers={"Retry-After": "2"}),
        Answer(body=body, headers={"X-Next-Page": "2"}),
    )
    clock = Clock()
    result = GitLabClient(host=HOST, project=PROJECT, token=TOKEN, now=clock, wait=clock.wait).commits(SINCE, 1)

    assert clock.waited == [2.0]
    assert result.next_page == 2 and result.rows[0]["id"] == "a1"


# ── The health check ─────────────────────────────────────────────────────────


def adapter(wire: Wire, **kwargs: Any) -> GitLabConnectorAdapter:
    kwargs.setdefault("now", lambda: NOW)
    kwargs.setdefault("credential", TOKEN)
    kwargs.setdefault("project", PROJECT)
    return GitLabConnectorAdapter(instance="gitlab:alpha", scope=SCOPE, transport=wire, **kwargs)


def test_a_health_check_asks_for_one_commit_and_is_ok_on_200():
    wire = Wire(page(commit("a1", at=NOW)))
    probe = adapter(wire).check_health()

    assert probe.health is Health.OK
    assert wire.sent[0].url == f"{COMMITS}?per_page=1"


@pytest.mark.parametrize(
    "status, reading", [(401, "refused the token"), (403, "read_api"), (404, "cannot see"), (503, "503")]
)
def test_a_health_check_fails_with_the_statuss_reading(status, reading):
    probe = adapter(Wire(answer(status))).check_health()

    assert probe.health is Health.FAILING
    assert reading in probe.detail
    assert probe.remediation
    assert TOKEN not in probe.detail + probe.remediation


def test_a_health_check_never_waits_out_a_throttle():
    clock = Clock()
    wire = Wire(answer(429, {"Retry-After": "5"}))
    probe = adapter(wire, now=clock, wait=clock.wait).check_health()

    assert probe.health is Health.FAILING
    assert clock.waited == [] and len(wire.sent) == 1


def test_a_silent_host_is_failing_at_the_registrys_bound():
    """Row: *health check, silent* — FAILING at the bound, never raised."""
    release = threading.Event()

    def silent(request: GitLabRequest) -> GitLabResponse:
        release.wait(30)
        return page()

    registry = ConnectorRegistry()
    registry.register(adapter(silent))  # type: ignore[arg-type]
    try:
        (probe,) = registry.check_health(timeout=0.3).probes
    finally:
        release.set()
    assert probe.health is Health.FAILING
    assert "abandoned" in probe.detail


def test_the_stub_state_no_longer_exists():
    """A credential and a transport that answers 200 is OK — nothing reports a
    stub any more, and nothing reports WARNING for a connector that answered."""
    probe = adapter(Wire(page())).check_health()
    assert probe.health is Health.OK
    assert "stub" not in probe.detail


# ── Composed through `build()` ───────────────────────────────────────────────


class _Keychain:
    """Custody that answers, so composition never reaches the real keychain."""

    def __init__(self) -> None:
        self._secret: bytes | None = b"K" * 32

    def store(self, name: str, secret: bytes) -> None:
        self._secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        from pm_ai.ports import KeyAlreadyEnrolled

        if self._secret is not None:
            raise KeyAlreadyEnrolled(name)
        self._secret = secret

    def fetch(self, name: str) -> bytes:
        from pm_ai.ports import KeyNotFound

        if self._secret is None:
            raise KeyNotFound(name)
        return self._secret

    def delete(self, name: str) -> None:
        self._secret = None


def _row(daemon: Any, instance: str, **settings: Any) -> None:
    """Write one GitLab `connectors/` row by hand, as an operator edits it."""
    row = {"instance": instance, "system": "gitlab", "enabled": True, **settings}
    daemon.storage.write_artifact(
        json.dumps(row).encode("utf-8"),
        scope=APPLICATION,
        artifact="connectors/",
        name=f"{instance}.json",
    )


def test_an_enrolled_row_harvests_two_pages_under_its_own_name(tmp_path):
    """Acceptance: `gitlab:alpha` for `acme/web` on a single-project machine.

    Both pages' events persist under `gitlab:alpha`, one window is saved under
    that name, and the cursor holds the newest commit date. Before 8n the
    connector called itself `gitlab:acme/web` and every save was refused, and
    its references carried the provider path the grammar refuses.
    """
    keychain = _Keychain()
    enrolling = build(tmp_path, "alpha", now=lambda: NOW, keychain=keychain)
    enrol_connector(
        enrolling.storage,
        system="gitlab",
        instance="gitlab:alpha",
        credential=TOKEN,
        probe=lambda system, credential: "gitlab accepted it",
    )
    _row(enrolling, "gitlab:alpha", project=PROJECT)

    daemon = build(tmp_path, "alpha", now=lambda: NOW, keychain=keychain)
    connector = daemon.connectors["gitlab:alpha"]
    assert isinstance(connector, GitLabConnectorAdapter)
    assert (connector.instance, connector.project, connector.credential) == (
        "gitlab:alpha",
        PROJECT,
        TOKEN,
    )
    wire = Wire(
        page(commit("p1a", at=NOW - timedelta(hours=3)), next_page=2),
        page(commit("p2a", at=NOW - timedelta(hours=1))),
        # Story 8t: the members, issues and merge requests, each empty.
        page(),
        page(),
        page(),
    )
    connector.transport = wire

    persisted = run_harvest(daemon, "gitlab:alpha")

    assert persisted.persisted == 2
    assert [wire.query(i)["page"] for i in (0, 1)] == [["1"], ["2"]]
    assert daemon.storage.coverage_windows("gitlab:alpha") == [(NOW, NOW)]
    stored = json.loads(daemon.storage.load_cursor("gitlab:alpha").token)
    assert stored["commits"] == "2026-08-19T08:00:00Z"
    log = "".join(
        daemon.storage.read_event_log_segment(scope=daemon.scope, name=name)
        for name in daemon.storage.event_log_segments(scope=daemon.scope)
    )
    assert "gitlab:alpha:commit:p1a" in log and "gitlab:alpha:commit:p2a" in log
    assert "acme/web" not in log


def test_a_hand_written_row_with_a_host_path_and_a_reach_back_is_what_goes_out(tmp_path):
    """The row's `host` and `first_run_reach_back_minutes`, read by composition,
    proven by the first recorded request."""
    _row(
        build(tmp_path, "alpha", now=lambda: NOW),
        "gitlab:alpha",
        project=PROJECT,
        host="https://git.internal/gitlab",
        first_run_reach_back_minutes=1440,
    )
    daemon = build(tmp_path, "alpha", now=lambda: NOW)
    connector = daemon.connectors["gitlab:alpha"]
    connector.credential = TOKEN
    wire = Wire(page(), page(), page(), page())
    connector.transport = wire

    result = connector.harvest(Cursor())

    # Story 8t: the work read beside the commits is a change, so the run is
    # HARVESTED while the commit walk's own window stands.
    assert result.outcome is HarvestOutcome.HARVESTED
    assert result.coverage is not None and result.work is not None
    assert urlsplit(wire.sent[1].url).path == "/gitlab/api/v4/projects/acme%2Fweb/members/all"
    sent = urlsplit(wire.sent[0].url)
    assert (sent.scheme, sent.netloc) == ("https", "git.internal")
    assert sent.path == "/gitlab/api/v4/projects/acme%2Fweb/repository/commits"
    assert wire.query()["since"] == ["2026-08-18T09:00:00Z"], "one day of reach-back"


def test_a_row_with_no_reach_back_asks_thirty_days_back_on_its_first_run(tmp_path):
    """Story 8s: enrolment writes no reach-back key, so a first run asks from
    `MERGE_LAG_DAYS` back — the span every later run asks from before its cursor."""
    _row(build(tmp_path, "alpha", now=lambda: NOW), "gitlab:alpha", project=PROJECT)
    connector = build(tmp_path, "alpha", now=lambda: NOW).connectors["gitlab:alpha"]
    connector.credential = TOKEN
    wire = Wire(page())
    connector.transport = wire

    connector.harvest(Cursor())

    assert wire.query()["since"] == ["2026-07-20T09:00:00Z"], "thirty days of reach-back"


def test_a_row_without_a_host_asks_gitlab_com_and_the_wait_is_a_real_sleep(tmp_path):
    import time

    _row(build(tmp_path, "alpha", now=lambda: NOW), "gitlab:alpha", project=PROJECT)
    connector = build(tmp_path, "alpha", now=lambda: NOW).connectors["gitlab:alpha"]

    assert connector.host == "https://gitlab.com"
    assert connector.wait is time.sleep, "an unwired wait loses every honoured hint"


def test_the_built_in_keeps_its_project_name_and_is_composed_to_wait(tmp_path):
    import time

    builtin = build(tmp_path, "alpha", now=lambda: NOW).connectors["gitlab:alpha"]
    assert builtin.instance == "gitlab:alpha"
    assert builtin.wait is time.sleep
    assert builtin.credential is None


@pytest.mark.parametrize(
    "settings, key",
    [
        ({"project": "/x"}, "project"),
        ({}, "project"),
        ({"project": PROJECT, "host": "http://gitlab.com"}, "host"),
        ({"project": PROJECT, "host": "https://gitlab.com/"}, "host"),
        ({"project": PROJECT, "first_run_reach_back_minutes": 0}, "first_run_reach_back_minutes"),
        ({"project": PROJECT, "first_run_reach_back_minutes": True}, "first_run_reach_back_minutes"),
        ({"project": PROJECT, "first_run_reach_back_minutes": "60"}, "first_run_reach_back_minutes"),
        ({"project": PROJECT, "first_run_reach_back_minutes": 10**12}, "first_run_reach_back_minutes"),
    ],
)
def test_a_bad_row_setting_leaves_the_connector_unbuilt_with_the_key_named(
    tmp_path, capsys, settings, key
):
    """Row: *bad row setting* — not built, never silently dropped, key on stderr.

    The enrolled row is named `gitlab:web` so the built-in `gitlab:alpha` stays
    beside it, and its absence is the enrolled one's.
    """
    _row(build(tmp_path, "alpha", now=lambda: NOW), "gitlab:web", **settings)
    capsys.readouterr()
    daemon = build(tmp_path, "alpha", now=lambda: NOW)

    assert "gitlab:web" not in daemon.connectors
    printed = capsys.readouterr().err
    assert "gitlab:web" in printed and key in printed and "GitLab connector" in printed


# ── Review round: bounds on what the provider controls ───────────────────────


def test_every_request_names_pm_ai_as_its_user_agent(monkeypatch):
    wire = Wire(page())
    client(wire).commits(SINCE, 1)
    assert wire.sent[0].headers["User-Agent"] == USER_AGENT == "pm-ai"

    sockets = _no_sockets(monkeypatch, Answer())
    _urllib_transport(wire.sent[0])
    assert sockets.opened[0].get_header("User-agent") == "pm-ai"


def test_an_oversized_retry_after_is_the_longest_hint_and_never_reaches_int():
    """Past `int()`'s 4300-digit limit a header value would raise; it is read as
    the longest hint instead, which no budget fits."""
    clock = Clock()
    wire = Wire(answer(429, {"Retry-After": "9" * 5000}))
    with pytest.raises(PageUnavailable) as refused:
        client(wire, now=clock, wait=clock.wait).commits(SINCE, 1)

    assert clock.waited == []
    assert refused.value.retryable is True
    assert refused.value.retry_after is not None
    assert refused.value.retry_after > timedelta(days=365)


@pytest.mark.parametrize("stated", ["9" * 5000, "1" + "0" * (MAX_HEADER_DIGITS)])
def test_an_oversized_next_page_is_unreadable(stated):
    refused = refusal(Wire(page(commit("a1", at=NOW), next_page=stated)))
    assert refused.retryable is False
    assert "next-page" in refused.reason


def test_a_body_past_the_cap_is_refused_unread(monkeypatch):
    big = b"[" + b" " * (MAX_BODY_BYTES + 10) + b"]"
    _no_sockets(monkeypatch, Answer(body=big))
    with pytest.raises(PageUnavailable) as refused:
        _urllib_transport(_request())
    assert refused.value.retryable is True
    assert str(MAX_BODY_BYTES) in refused.value.reason


def test_an_error_body_past_the_cap_is_refused_too(monkeypatch):
    _no_sockets(monkeypatch, _error(503, body=b"x" * (MAX_BODY_BYTES + 1)))
    with pytest.raises(PageUnavailable) as refused:
        _urllib_transport(_request())
    assert refused.value.retryable is True and "cap" in refused.value.reason


class _ResetWhileReading(io.RawIOBase):
    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        raise ConnectionResetError(f"reset while reading {URL}")


@pytest.mark.parametrize("status", [429, 503])
def test_a_reset_while_reading_an_error_body_is_classified_not_escaped(status, monkeypatch):
    """The read inside the error branch is guarded as a 200's is: retryable,
    the exception type named, its text not."""
    failing = urllib.error.HTTPError(
        URL, status, "x", _headers({}), io.BufferedReader(_ResetWhileReading())
    )
    _no_sockets(monkeypatch, failing)
    with pytest.raises(PageUnavailable) as refused:
        _urllib_transport(_request())

    assert refused.value.retryable is True
    assert "ConnectionResetError" in refused.value.reason
    assert "reset while reading" not in refused.value.reason


def test_a_reset_while_reading_a_200_body_is_classified_the_same_way(monkeypatch):
    class Resetting(Answer):
        def read(self, size: int = -1) -> bytes:
            raise ConnectionResetError("reset")

    _no_sockets(monkeypatch, Resetting())
    with pytest.raises(PageUnavailable) as refused:
        _urllib_transport(_request())
    assert refused.value.retryable is True and "ConnectionResetError" in refused.value.reason


def test_the_last_request_waits_no_longer_than_the_budget_has_left(monkeypatch):
    """The socket timeout is thirty seconds, or what is left of the run if less."""
    wire = Wire(page(), page())
    client(wire).commits(SINCE, 2, deadline=NOW + timedelta(seconds=7))
    client(wire).commits(SINCE, 2, deadline=NOW + timedelta(minutes=5))
    assert [request.timeout for request in wire.sent] == [7.0, 30.0]

    sockets = _no_sockets(monkeypatch, Answer())
    _urllib_transport(wire.sent[0])
    assert sockets.timeouts == [7.0]


def test_nothing_is_sent_when_no_budget_remains():
    wire = Wire(page())
    with pytest.raises(PageUnavailable) as refused:
        client(wire).commits(SINCE, 2, deadline=NOW)
    assert wire.sent == [] and refused.value.retryable is True


def test_a_wait_that_spends_the_budget_is_not_followed_by_a_second_request():
    """`deadline == now + hint`: the hint fits, is waited, and then nothing is
    left — so the page is not re-asked, and the run ends retryable."""
    clock = Clock()
    wire = Wire(answer(429, {"Retry-After": "5"}), page())
    with pytest.raises(PageUnavailable) as refused:
        client(wire, now=clock, wait=clock.wait).commits(
            SINCE, 2, deadline=NOW + timedelta(seconds=5)
        )
    assert clock.waited == [5.0]
    assert len(wire.sent) == 1
    assert refused.value.retryable is True


def test_an_oversleeping_wait_is_not_followed_by_a_second_request():
    clock = Clock()

    def oversleeps(seconds: float) -> None:
        clock.wait(seconds * 100)

    wire = Wire(answer(429, {"Retry-After": "5"}), page())
    with pytest.raises(PageUnavailable):
        client(wire, now=clock, wait=oversleeps).commits(
            SINCE, 2, deadline=NOW + timedelta(seconds=60)
        )
    assert len(wire.sent) == 1


# ── Review round: through the adapter ────────────────────────────────────────


def test_an_adapter_harvest_waits_out_a_throttle_on_page_two():
    """`wait=` reaches the client: page 2 throttled, waited, re-asked."""
    clock = Clock()
    wire = Wire(
        page(commit("a1", at=NOW - timedelta(hours=2)), next_page=2),
        answer(429, {"Retry-After": "5"}),
        page(commit("a2", at=NOW - timedelta(hours=1))),
    )
    result = adapter(wire, now=clock, wait=clock.wait).harvest(Cursor())

    assert result.outcome is HarvestOutcome.HARVESTED
    assert clock.waited == [5.0]
    assert [wire.query(i)["page"] for i in range(3)] == [["1"], ["2"], ["2"]]
    assert not wire.overrun


def test_a_hint_that_fits_a_fresh_budget_but_not_the_runs_remainder_ends_the_walk():
    """`deadline=` reaches the client: the run's budget is a minute, page 2's
    request takes thirty seconds to come back throttled, and a 45-second hint
    — which a fresh budget would hold — does not fit what is left."""
    clock = Clock()
    wire = Wire(
        page(commit("a1", at=NOW - timedelta(hours=2)), next_page=2),
        answer(429, {"Retry-After": "45"}),
    )

    def slow_second_page(request: GitLabRequest) -> GitLabResponse:
        if wire.sent:
            clock.at += timedelta(seconds=30)
        return wire(request)

    result = adapter(
        slow_second_page,  # type: ignore[arg-type]
        now=clock,
        wait=clock.wait,
        budget=timedelta(seconds=60),
    ).harvest(Cursor())

    assert clock.waited == []
    assert result.outcome is HarvestOutcome.FAILED
    assert result.failure is not None and result.failure.retryable is True
    assert result.failure.retry_after == timedelta(seconds=45)
    assert len(result.events) == 1
    assert not wire.overrun


def test_a_wire_that_runs_out_of_answers_fails_the_test_through_every_catch():
    """An extra request is not swallowed by the client's or the harvest's
    broad catches: `WireOverrun` is a `BaseException`."""
    wire = Wire(page(commit("a1", at=NOW), next_page=2))
    with pytest.raises(WireOverrun):
        adapter(wire).harvest(Cursor())
    assert wire.overrun


# ── Review round: what a failed health check says to do ──────────────────────


@pytest.mark.parametrize(
    "status, says",
    [
        (401, "new personal access token"),
        (403, "read_api"),
        (404, "connectors/gitlab:alpha.json"),
        (429, "later"),
    ],
)
def test_each_status_names_its_own_remedy(status, says):
    probe = adapter(Wire(answer(status))).check_health()
    assert says in probe.remediation
    if status == 404:
        assert "`project`" in probe.remediation and "`host`" in probe.remediation


@pytest.mark.parametrize(
    "settings",
    [{"host": "http://gitlab.com"}, {"project": "/acme"}, {"credential": "glpat-a\nb"}],
    ids=["host", "project", "token"],
)
def test_a_setting_that_cannot_be_sent_names_the_settings_not_the_network(settings):
    wire = Wire()
    probe = adapter(wire, **settings).check_health()

    assert probe.health is Health.FAILING
    assert wire.sent == []
    assert "connectors/gitlab:alpha.json" in probe.remediation
    assert "network is not the problem" in probe.remediation
