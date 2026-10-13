"""Story 8o — `pm-ai connector add gitlab <name>`, one test per matrix row.

Every row runs `entry.main` against a real temporary home, so the sealed
credentials file, its encryption, its claim and the `connectors/` row are the
real ones. Two things are faked: the keychain, and GitLab — at the transport,
through `GitLabEnrolment.transport`, or one level lower, at `urllib`'s opener,
where the real transport's own redirect and no-answer handling is the point. No
test opens a socket.

The questions are answered by replacing `input` and the hidden prompt by
replacing `getpass.getpass`, both of which record every prompt, so "asked
again" and "refused before any question" are assertions about what was asked
rather than about what was printed.
"""

from __future__ import annotations

import functools
import io
import json
import logging
import stat
import threading
import time
import urllib.error
from collections.abc import Callable
from pathlib import Path

import pytest

from pm_ai.app import entry
from pm_ai.app.wiring import Bootstrap, GitLabEnrolment, bootstrap, build
from pm_ai.connectors.gitlab import GitLabConnectorAdapter
from pm_ai.connectors.gitlab import client as gitlab_client
from pm_ai.connectors.gitlab.client import GitLabRequest, GitLabResponse, PageUnavailable
from pm_ai.core.connector_enrolment import enrol_connector, stored_credentials
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.domain.health import ArtifactState, Health
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.storage_tiers import RESTRICTED_FILE_MODE
from pm_ai.ports import KeyAlreadyEnrolled, KeychainUnavailable, KeyNotFound
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL

INSTANCE = "gitlab:alpha"
TOKEN = "glpat-the-token-typed-at-the-hidden-prompt"
KEY = b"K" * 32
APPLICATION = DataScope(ScopeKind.APPLICATION)
DEFAULT_HOST = "https://gitlab.com"

VALID = ["", "acme/web"]
"""Enter for the host, then the project path."""

ONE_COMMIT = [{"id": "9f2a1c" + "0" * 34, "message": "a commit", "committed_date": "2026-10-01T09:00:00Z"}]


class Keychain:
    """One master key, already enrolled unless `secret=None`; `refuses` makes it unreadable."""

    def __init__(self, secret: bytes | None = KEY) -> None:
        self.secret = secret
        self.refuses: BaseException | None = None

    def store(self, name: str, secret: bytes) -> None:
        self.secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if self.secret is not None:
            raise KeyAlreadyEnrolled(name)
        self.secret = secret

    def fetch(self, name: str) -> bytes:
        if self.refuses is not None:
            raise self.refuses
        if self.secret is None:
            raise KeyNotFound(name)
        return self.secret

    def delete(self, name: str) -> None:
        self.secret = None


class Terminal:
    """`input`, answering from a script and recording every prompt it was shown.

    Running out of answers is Ctrl-D.
    """

    def __init__(self, answers: list[str | BaseException]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class Hidden:
    """`getpass.getpass`: one answer (or what it raises), every prompt recorded."""

    def __init__(self, answer: str | BaseException = TOKEN) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "", stream=None) -> str:
        self.prompts.append(prompt)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class GitLab:
    """The transport: one answer for every request, every request recorded.

    `answer` is a response, or an exception raised as the real transport
    raises one; `hold` makes the request wait until it is set (or five
    seconds pass), so a test can release a stand-in it abandoned.
    """

    def __init__(
        self,
        answer: GitLabResponse | BaseException | None = None,
        *,
        hold: threading.Event | None = None,
        then: Callable[[], None] | None = None,
    ) -> None:
        self.answer = answer if answer is not None else GitLabResponse(200, list(ONE_COMMIT))
        self.hold = hold
        self.then = then
        self.requests: list[GitLabRequest] = []

    def __call__(self, request: GitLabRequest) -> GitLabResponse:
        self.requests.append(request)
        if self.hold is not None:
            self.hold.wait(5)
        if self.then is not None:
            self.then()
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _machine(tmp_path, monkeypatch, projects: tuple[str, ...]) -> tuple[Path, Keychain]:
    keychain = Keychain()
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: keychain)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    entries = {}
    for project in projects:
        repository = tmp_path / f"repo-{project}"
        repository.mkdir()
        entries[project] = ProjectEntry(path=repository)

    def read(chain):
        return Bootstrap(
            entries,
            ArtifactState.read(render_registry(entries)) if entries else ArtifactState.absent(),
            bootstrap(chain).config,
        )

    monkeypatch.setattr(entry, "_bootstrap", read)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.chdir(tmp_path)
    return home, keychain


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """One enrolled project, `alpha`, a master key, a terminal, and an empty home."""
    return _machine(tmp_path, monkeypatch, ("alpha",))


def _gitlab(monkeypatch, provider: GitLab | None, *, timeout: float = 10.0) -> None:
    """Make the composition root's `GitLabEnrolment` talk to `provider`.

    `None` keeps the real transport, for a test that fakes `urllib`'s opener.
    """
    monkeypatch.setattr(
        entry,
        "GitLabEnrolment",
        functools.partial(GitLabEnrolment, transport=provider, timeout=timeout),
    )


def _add(
    monkeypatch,
    answers: list,
    *,
    token: str | BaseException = TOKEN,
    instance: str = INSTANCE,
) -> tuple[int, Terminal, Hidden]:
    terminal = Terminal(answers)
    hidden = Hidden(token)
    monkeypatch.setattr("builtins.input", terminal)
    monkeypatch.setattr("getpass.getpass", hidden)
    return entry.main(["connector", "add", "gitlab", instance]), terminal, hidden


def _settings_file(home: Path, instance: str = INSTANCE) -> Path:
    return home / ".pm-ai" / "connectors" / f"{instance}.json"


def _row(home: Path, instance: str = INSTANCE) -> dict:
    return json.loads(_settings_file(home, instance).read_text())


def _sealed(home: Path, keychain: Keychain) -> dict:
    daemon = build(home, APPLICATION, keychain=keychain)
    return stored_credentials(daemon.storage)


def _nothing_saved(home: Path) -> None:
    connectors = home / ".pm-ai" / "connectors"
    assert not connectors.exists() or not any(connectors.iterdir()), "a row was written"
    assert not (home / ".pm-ai" / "private" / "config.json").exists(), (
        "a credential was sealed"
    )


def _no_token_on_screen(captured) -> None:
    assert TOKEN not in captured.out and TOKEN not in captured.err, "the token was printed"
    assert TOKEN[:12] not in captured.out and TOKEN[:12] not in captured.err


def _prompts(terminal: Terminal, starting: str) -> list[str]:
    return [p for p in terminal.prompts if p.startswith(starting)]


# ── Happy path ───────────────────────────────────────────────────────────────


def test_a_valid_add_saves_the_settings_and_the_sealed_token_together(
    machine, monkeypatch, capsys
):
    home, keychain = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, terminal, hidden = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(terminal.prompts) == 2, terminal.prompts
    assert terminal.prompts[0].startswith("GitLab host") and DEFAULT_HOST in terminal.prompts[0]
    assert "group/project" in terminal.prompts[1]
    assert len(hidden.prompts) == 1 and "read_api" in hidden.prompts[0], (
        "the token prompt names the scope it needs"
    )
    assert "enrolled and healthy" in captured.out
    assert "next start" in captured.out
    assert str(_settings_file(home)) in captured.out, "the closing message names the file"
    _no_token_on_screen(captured)

    (request,) = provider.requests
    assert request.url == (
        f"{DEFAULT_HOST}/api/v4/projects/acme%2Fweb/repository/commits?per_page=1"
    ), "one commit of that project on that host"
    assert request.headers["PRIVATE-TOKEN"] == TOKEN
    assert request.timeout == 30, "the transport's socket timeout"

    row = _row(home)
    assert row == {
        "instance": INSTANCE,
        "system": "gitlab",
        "enabled": True,
        "project": "acme/web",
        "host": DEFAULT_HOST,
    }
    assert "first_run_reach_back_minutes" not in row, "the reach-back is not asked"
    assert _sealed(home, keychain) == {INSTANCE: {"system": "gitlab", "credential": TOKEN}}


def test_the_token_is_stripped_before_it_is_checked_and_sealed(
    machine, monkeypatch, capsys
):
    home, keychain = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, _, _ = _add(monkeypatch, VALID, token=f"  {TOKEN}\n")

    assert code == EXIT_OK, capsys.readouterr().err
    assert provider.requests[0].headers["PRIVATE-TOKEN"] == TOKEN
    assert _sealed(home, keychain)[INSTANCE]["credential"] == TOKEN


def test_the_settings_file_is_written_at_0600(machine, monkeypatch, capsys):
    home, _ = machine
    _gitlab(monkeypatch, GitLab())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK, capsys.readouterr().err
    assert stat.S_IMODE(_settings_file(home).stat().st_mode) == RESTRICTED_FILE_MODE


# ── The new connector builds, in the built-in's place ────────────────────────


def test_a_fresh_build_holds_the_enrolled_connector_in_the_builtins_place(
    machine, monkeypatch, capsys
):
    home, keychain = machine
    _gitlab(monkeypatch, GitLab())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK, capsys.readouterr().err

    daemon = build(home, "alpha", keychain=keychain)

    gitlab = [c for c in daemon.connectors.values() if isinstance(c, GitLabConnectorAdapter)]
    assert len(gitlab) == 1, "the built-in and the enrolled connector both stood"
    (connector,) = gitlab
    assert connector is daemon.connectors[INSTANCE]
    assert connector.project == "acme/web"
    assert connector.host == DEFAULT_HOST
    assert connector.credential == TOKEN, "the enrolled one, holding the credential"
    assert connector.scope == DataScope(ScopeKind.PROJECT, "alpha")
    assert daemon.unbuilt == ()


def _opener(answer: Callable[[], object]):
    """`urllib`'s opener, faked one level under the real transport."""

    class Opener:
        def open(self, request, timeout=None):
            return answer()

    return lambda: Opener()


class _Answer(io.BytesIO):
    """What `urllib` returns: a body, a status, headers, the URL it landed on."""

    def __init__(self, url: str, status: int, body: bytes) -> None:
        super().__init__(body)
        self.url = url
        self.status = status
        self.headers = {"Content-Type": "application/json"}


def test_connector_check_lists_the_added_connector_as_healthy(
    machine, monkeypatch, capsys
):
    _gitlab(monkeypatch, GitLab())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK
    capsys.readouterr()

    asked: list[str] = []

    def answer():
        url = f"{DEFAULT_HOST}/api/v4/projects/acme%2Fweb/repository/commits?per_page=1"
        asked.append(url)
        return _Answer(url, 200, json.dumps(ONE_COMMIT).encode())

    monkeypatch.setattr(gitlab_client, "_no_redirect_opener", _opener(answer))
    code = entry.main(["connector", "check"])

    captured = capsys.readouterr()
    (line,) = [line for line in captured.out.splitlines() if f"] {INSTANCE}:" in line]
    assert f"[{Health.OK.value:>7}]" in line, captured.out
    assert code == EXIT_OK, captured.out
    assert asked, "connector check did not ask GitLab"
    _no_token_on_screen(captured)


# ── Refused before any question ──────────────────────────────────────────────


def _asked_nothing(terminal: Terminal, hidden: Hidden) -> None:
    assert terminal.prompts == [], "a question was asked"
    assert hidden.prompts == [], "the token was asked for"


def test_a_name_in_use_is_refused_before_any_question(machine, monkeypatch, capsys):
    _gitlab(monkeypatch, GitLab())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK
    capsys.readouterr()

    code, terminal, hidden = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    _asked_nothing(terminal, hidden)
    err = capsys.readouterr().err
    assert "already configured" in err and INSTANCE in err


def test_a_name_in_use_is_refused_before_the_terminal_and_the_keychain(
    machine, monkeypatch, capsys
):
    """The row is on disk, so it is the first thing said — before either."""
    home, keychain = machine
    _gitlab(monkeypatch, GitLab())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK
    capsys.readouterr()
    keychain.secret = None
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code, terminal, hidden = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    _asked_nothing(terminal, hidden)
    assert "already configured" in capsys.readouterr().err


def test_a_half_enrolled_name_is_refused_naming_the_repair(machine, monkeypatch, capsys):
    home, keychain = machine
    daemon = build(home, "alpha", keychain=keychain)
    enrol_connector(
        daemon.storage,
        system="gitlab",
        instance=INSTANCE,
        credential="an-earlier-token",
        probe=lambda system, credential: "accepted",
    )
    _settings_file(home).unlink()
    _gitlab(monkeypatch, GitLab())

    code, terminal, hidden = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    _asked_nothing(terminal, hidden)
    err = capsys.readouterr().err
    assert "half-finished enrolment" in err
    assert "Remove the" in err, "the repair is named"


def test_a_machine_with_no_master_key_is_refused_before_any_question(
    machine, monkeypatch, capsys
):
    home, keychain = machine
    keychain.secret = None
    _gitlab(monkeypatch, GitLab())

    code, terminal, hidden = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    _asked_nothing(terminal, hidden)
    assert "pm-ai key enrol" in capsys.readouterr().err
    _nothing_saved(home)


def test_a_keychain_that_cannot_be_read_is_refused_before_any_question(
    machine, monkeypatch, capsys
):
    home, keychain = machine
    keychain.refuses = KeychainUnavailable("the login keychain is locked")
    _gitlab(monkeypatch, GitLab())

    code, terminal, hidden = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    _asked_nothing(terminal, hidden)
    assert "the login keychain is locked" in capsys.readouterr().err
    _nothing_saved(home)


def test_without_a_terminal_nothing_is_asked(machine, monkeypatch, capsys):
    home, _ = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code, terminal, hidden = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    _asked_nothing(terminal, hidden)
    assert "TTY" in capsys.readouterr().err
    assert provider.requests == []
    _nothing_saved(home)


def test_a_cli_composed_with_no_gitlab_check_refuses_by_name(
    machine, monkeypatch, capsys
):
    home, _ = machine
    monkeypatch.setattr(entry, "GitLabEnrolment", lambda **named: None)

    code, terminal, hidden = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    _asked_nothing(terminal, hidden)
    assert "no GitLab check was supplied" in capsys.readouterr().err
    _nothing_saved(home)


# ── Where the name places the connector ──────────────────────────────────────


def test_on_a_machine_watching_two_projects_an_unmatched_name_is_refused(
    tmp_path, monkeypatch, capsys
):
    home, _ = _machine(tmp_path, monkeypatch, ("alpha", "beta"))
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, terminal, hidden = _add(monkeypatch, VALID, instance="gitlab:other")

    assert code == EXIT_REFUSAL
    _asked_nothing(terminal, hidden)
    err = capsys.readouterr().err
    assert "gitlab:other" in err
    assert "gitlab:alpha" in err and "gitlab:beta" in err, "the names that would build"
    assert "gitlab:<enrolled project id>" in err
    assert provider.requests == []
    _nothing_saved(home)


def test_on_a_machine_watching_two_projects_a_projects_own_name_is_enrolled(
    tmp_path, monkeypatch, capsys
):
    home, keychain = _machine(tmp_path, monkeypatch, ("alpha", "beta"))
    _gitlab(monkeypatch, GitLab())

    code, _, _ = _add(monkeypatch, VALID, instance="gitlab:beta")

    assert code == EXIT_OK, capsys.readouterr().err
    daemon = build(home, APPLICATION, watched=("alpha", "beta"), keychain=keychain)
    connector = daemon.connectors["gitlab:beta"]
    assert isinstance(connector, GitLabConnectorAdapter)
    assert connector.credential == TOKEN
    assert connector.scope == DataScope(ScopeKind.PROJECT, "beta")


def test_with_one_project_any_name_is_enrolled_into_it(machine, monkeypatch, capsys):
    home, keychain = machine
    _gitlab(monkeypatch, GitLab())

    code, _, _ = _add(monkeypatch, VALID, instance="gitlab:web")

    assert code == EXIT_OK, capsys.readouterr().err
    daemon = build(home, "alpha", keychain=keychain)
    assert daemon.connectors["gitlab:web"].scope == DataScope(ScopeKind.PROJECT, "alpha")


def test_with_no_project_enrolled_the_add_is_saved_and_built_once_one_is(
    tmp_path, monkeypatch, capsys
):
    home, keychain = _machine(tmp_path, monkeypatch, ())
    _gitlab(monkeypatch, GitLab())

    code, _, _ = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert _row(home)["project"] == "acme/web"
    assert _sealed(home, keychain)[INSTANCE]["credential"] == TOKEN

    unwatched = build(home, APPLICATION, keychain=keychain)
    assert INSTANCE not in unwatched.connectors
    (unbuilt,) = unwatched.unbuilt
    assert unbuilt.instance == INSTANCE and "no project is enrolled" in unbuilt.reason

    watched = build(home, "alpha", keychain=keychain)
    assert watched.connectors[INSTANCE].credential == TOKEN


# ── The two questions ────────────────────────────────────────────────────────


def test_a_host_with_a_path_is_accepted_and_saved_as_typed(machine, monkeypatch, capsys):
    home, _ = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, _, _ = _add(monkeypatch, ["https://git.internal/gitlab", "acme/web"])

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row(home)["host"] == "https://git.internal/gitlab"
    assert provider.requests[0].url.startswith(
        "https://git.internal/gitlab/api/v4/projects/acme%2Fweb/"
    )


@pytest.mark.parametrize(
    ("typed", "said"),
    [
        ("http://git.internal", "https"),
        ("https://x/?a", "query"),
        ("https://git.internal/", "'/'"),
        ("https://git.internal#top", "fragment"),
        ("https://someone@git.internal", "user info"),
    ],
    ids=["http", "a query", "a trailing slash", "a fragment", "user info"],
)
def test_a_host_that_would_not_build_is_asked_again_with_the_reason(
    machine, monkeypatch, capsys, typed, said
):
    home, _ = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, terminal, _ = _add(monkeypatch, [typed, "", "acme/web"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_prompts(terminal, "GitLab host")) == 2, "asked again"
    assert said in captured.err and typed in captured.err
    assert _row(home)["host"] == DEFAULT_HOST
    assert len(provider.requests) == 1, "nothing was asked of the host refused"


@pytest.mark.parametrize("typed", ["web", "/acme/web", "acme/web?x", "acme/web#x", "acme/my web", ""])
def test_a_project_path_that_would_not_build_is_asked_again_showing_the_form(
    machine, monkeypatch, capsys, typed
):
    home, _ = machine
    _gitlab(monkeypatch, GitLab())

    code, terminal, _ = _add(monkeypatch, ["", typed, "acme/web"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_prompts(terminal, "GitLab project path")) == 2, "asked again"
    assert "group/project" in captured.err
    assert _row(home)["project"] == "acme/web"


def test_an_interruption_at_a_question_saves_nothing(machine, monkeypatch, capsys):
    home, _ = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, _, hidden = _add(monkeypatch, ["", KeyboardInterrupt()])

    assert code == EXIT_REFUSAL
    assert "nothing was saved" in capsys.readouterr().err
    assert hidden.prompts == []
    assert provider.requests == []
    _nothing_saved(home)


# ── The token ────────────────────────────────────────────────────────────────


def test_a_blank_token_is_refused_and_nothing_is_saved(machine, monkeypatch, capsys):
    home, _ = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, _, hidden = _add(monkeypatch, VALID, token="   ")

    assert code == EXIT_REFUSAL
    assert len(hidden.prompts) == 1, "asked once, not again"
    assert "no token was typed" in capsys.readouterr().err
    assert provider.requests == []
    _nothing_saved(home)


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), EOFError()], ids=["ctrl-c", "ctrl-d"])
def test_an_interruption_at_the_token_saves_nothing(
    machine, monkeypatch, capsys, interruption
):
    home, _ = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, _, _ = _add(monkeypatch, VALID, token=interruption)

    assert code == EXIT_REFUSAL
    err = capsys.readouterr().err
    assert "interrupted" in err and "nothing was saved" in err
    assert provider.requests == []
    _nothing_saved(home)


# ── GitLab's answers ─────────────────────────────────────────────────────────


def _refused_with(machine, monkeypatch, capsys, provider: GitLab, **kwargs) -> str:
    home, _ = machine
    _gitlab(monkeypatch, provider, **kwargs)
    code, _, _ = _add(monkeypatch, VALID)
    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.out
    _no_token_on_screen(captured)
    _nothing_saved(home)
    return captured.err


def test_a_refused_token_saves_nothing(machine, monkeypatch, capsys):
    err = _refused_with(
        machine, monkeypatch, capsys, GitLab(GitLabResponse(401, {"message": "401 Unauthorized"}))
    )
    assert "GitLab refused the token" in err


def test_a_token_without_read_api_refuses_enrolment(machine, monkeypatch, capsys):
    """403 refuses rather than warns: a connector that cannot harvest is never sealed."""
    err = _refused_with(
        machine, monkeypatch, capsys, GitLab(GitLabResponse(403, {"message": "403 Forbidden"}))
    )
    assert "read_api" in err
    assert "enrolled and" not in err


def test_a_project_not_found_names_the_path_and_the_host(machine, monkeypatch, capsys):
    err = _refused_with(
        machine,
        monkeypatch,
        capsys,
        GitLab(GitLabResponse(404, {"message": "404 Project Not Found"})),
    )
    assert "'acme/web'" in err and DEFAULT_HOST in err
    assert "The path, not the token" in err


def _unreachable(err: str) -> None:
    assert "try later" in err and "the token was not judged" in err, err
    assert "refused the token" not in err
    assert "read_api" not in err
    assert err.count("unreachable") <= 1, "said twice"
    assert "not the credential" not in err, "the same thing said twice"


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (GitLabResponse(429, {"message": "Retry later"}, {"Retry-After": "60"}), "GitLab answered 429"),
        (GitLabResponse(500, None), "GitLab answered 500 (a server error)"),
        (GitLabResponse(503, {"message": "down"}), "GitLab answered 503 (a server error)"),
        (ConnectionRefusedError(61, "Connection refused"), "unreachable: no answer came back (ConnectionRefusedError)"),
    ],
    ids=["429", "500", "503", "connection refused from the wire"],
)
def test_an_unreachable_gitlab_is_not_a_refused_token(
    machine, monkeypatch, capsys, answer, said
):
    err = _refused_with(machine, monkeypatch, capsys, GitLab(answer))
    _unreachable(err)
    assert said in err


def test_a_gitlab_silent_past_the_bound_is_unreachable(machine, monkeypatch, capsys):
    released = threading.Event()
    try:
        started = time.monotonic()
        err = _refused_with(
            machine, monkeypatch, capsys, GitLab(hold=released), timeout=0.3
        )
        assert time.monotonic() - started < 8.0, "the bound did not hold"
    finally:
        released.set()
    _unreachable(err)
    assert "unreachable" in err and "0.3s" in err


@pytest.mark.parametrize(
    "answer",
    [
        GitLabResponse(418, {"message": "I'm a teapot"}),
        ValueError("a transport fault pm-ai cannot classify"),
        PageUnavailable("an answer larger than the cap", retryable=True),
    ],
    ids=["418", "an unclassified exception", "retryable with no OSError behind it"],
)
def test_anything_else_is_refused_not_called_unreachable(
    machine, monkeypatch, capsys, answer
):
    err = _refused_with(machine, monkeypatch, capsys, GitLab(answer))
    for unsaid in ("unreachable", "try later", "Try later", "not the credential"):
        assert unsaid not in err, unsaid
    assert "Nothing was saved" in err
    if isinstance(answer, ValueError):
        assert "ValueError" in err, "the exception is named by type"


def test_an_exception_out_of_the_check_itself_is_refused_by_type(
    machine, monkeypatch, capsys
):
    def broken(self):
        raise RuntimeError("something inside the client")

    monkeypatch.setattr(gitlab_client.GitLabClient, "check", broken)
    err = _refused_with(machine, monkeypatch, capsys, GitLab())
    assert "RuntimeError" in err and "Nothing was saved" in err
    assert "unreachable" not in err
    assert "Traceback" not in err


def test_a_host_that_cannot_be_reached_is_unreachable_through_the_real_transport(
    machine, monkeypatch, capsys
):
    """The real `urllib` transport, its opener faked: the wire's own reading."""

    def refused():
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(gitlab_client, "_no_redirect_opener", _opener(refused))
    _unreachable(_refused_with(machine, monkeypatch, capsys, None))


def test_a_redirect_is_refused_asking_for_the_final_address(machine, monkeypatch, capsys):
    """Through the real transport, which refuses a redirect rather than following it."""

    def redirect():
        raise urllib.error.HTTPError(
            f"{DEFAULT_HOST}/api/v4/projects/acme%2Fweb/repository/commits?per_page=1",
            302,
            "Found",
            {"Location": "https://elsewhere.example/"},
            io.BytesIO(b""),
        )

    monkeypatch.setattr(gitlab_client, "_no_redirect_opener", _opener(redirect))
    err = _refused_with(machine, monkeypatch, capsys, None)
    assert "302" in err and "redirect" in err
    assert "final address" in err
    assert "unreachable" not in err


@pytest.mark.parametrize("body", [{}, {"message": "hello"}, None, "a page"])
def test_a_200_that_is_not_a_list_is_not_a_gitlab_api_answer(
    machine, monkeypatch, capsys, body
):
    err = _refused_with(machine, monkeypatch, capsys, GitLab(GitLabResponse(200, body)))
    assert "not a GitLab API answer" in err
    assert "unreachable" not in err


def test_an_empty_project_is_healthy(machine, monkeypatch, capsys):
    """A list with no commits is still GitLab's answer for that project."""
    home, _ = machine
    _gitlab(monkeypatch, GitLab(GitLabResponse(200, [])))
    assert _add(monkeypatch, VALID)[0] == EXIT_OK, capsys.readouterr().err
    assert _row(home)["project"] == "acme/web"


def test_a_refusal_quoting_the_token_does_not_print_it(machine, monkeypatch, capsys):
    """The transport promises never to quote the token; enrolment does not rely on it."""
    err = _refused_with(
        machine,
        monkeypatch,
        capsys,
        GitLab(PageUnavailable(f"the wire said {TOKEN}", retryable=False)),
    )
    assert "withheld because it quoted the token" in err


# ── Every other system ───────────────────────────────────────────────────────


@pytest.mark.parametrize("system", ["jira", "GitLab"])
def test_another_system_is_refused_by_name_before_any_prompt(
    machine, monkeypatch, capsys, system
):
    home, _ = machine
    hidden = Hidden()
    terminal = Terminal([])
    monkeypatch.setattr("getpass.getpass", hidden)
    monkeypatch.setattr("builtins.input", terminal)

    code = entry.main(["connector", "add", system, "j:one"])

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    _asked_nothing(terminal, hidden)
    assert repr(system) in captured.err
    assert "'gitlab'" in captured.err and "'graph'" in captured.err
    _nothing_saved(home)


# ── Review round: interruptions, text, placement at the save ─────────────────


def test_an_interruption_at_the_host_question_saves_nothing(machine, monkeypatch, capsys):
    home, _ = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, terminal, hidden = _add(monkeypatch, [EOFError()])

    assert code == EXIT_REFUSAL
    assert "nothing was saved" in capsys.readouterr().err
    assert len(terminal.prompts) == 1 and hidden.prompts == []
    assert provider.requests == []
    _nothing_saved(home)


def _not_text() -> UnicodeDecodeError:
    return UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


@pytest.mark.parametrize("at", ["host", "project"])
def test_an_answer_that_is_not_text_is_asked_again(machine, monkeypatch, capsys, at):
    home, _ = machine
    _gitlab(monkeypatch, GitLab())
    answers = [_not_text(), "", "acme/web"] if at == "host" else ["", _not_text(), "acme/web"]

    code, terminal, _ = _add(monkeypatch, answers)

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(terminal.prompts) == 3, "asked again"
    assert "not valid text" in captured.err
    assert _row(home)["project"] == "acme/web"


def test_a_token_that_is_not_text_is_asked_again(machine, monkeypatch, capsys):
    home, keychain = machine
    _gitlab(monkeypatch, GitLab())
    tries = [_not_text(), TOKEN]
    prompts: list[str] = []

    def hidden(prompt: str = "", stream=None) -> str:
        prompts.append(prompt)
        answer = tries.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", Terminal(list(VALID)))
    monkeypatch.setattr("getpass.getpass", hidden)
    code = entry.main(["connector", "add", "gitlab", INSTANCE])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(prompts) == 2 and "not valid text" in captured.err
    assert _sealed(home, keychain)[INSTANCE]["credential"] == TOKEN


def test_ctrl_c_during_the_keychain_read_saves_nothing(machine, monkeypatch, capsys):
    home, keychain = machine
    keychain.refuses = KeyboardInterrupt()
    _gitlab(monkeypatch, GitLab())

    code, terminal, hidden = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.err
    assert "interrupted; nothing was saved" in captured.err
    assert "Traceback" not in captured.err
    _asked_nothing(terminal, hidden)
    _nothing_saved(home)


def test_ctrl_c_during_the_check_saves_nothing(machine, monkeypatch, capsys):
    home, _ = machine
    _gitlab(monkeypatch, GitLab(KeyboardInterrupt()))

    code, _, _ = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.err
    assert "interrupted; nothing was saved" in captured.err
    assert "Traceback" not in captured.err
    _nothing_saved(home)


def test_a_project_enrolled_during_the_questions_is_placed_against_at_the_save(
    tmp_path, monkeypatch, capsys
):
    """One project when asked, two by the save: `gitlab:web` would never build."""
    home, _ = _machine(tmp_path, monkeypatch, ("alpha",))
    entries = {"alpha": ProjectEntry(path=tmp_path / "repo-alpha")}

    def read(chain):
        return Bootstrap(
            dict(entries), ArtifactState.read(render_registry(entries)), bootstrap(chain).config
        )

    monkeypatch.setattr(entry, "_bootstrap", read)

    def enrol_beta() -> None:
        (tmp_path / "repo-beta").mkdir(exist_ok=True)
        entries["beta"] = ProjectEntry(path=tmp_path / "repo-beta")

    _gitlab(monkeypatch, GitLab(then=enrol_beta))

    code, _, _ = _add(monkeypatch, VALID, instance="gitlab:web")

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.out
    assert "gitlab:alpha" in captured.err and "gitlab:beta" in captured.err
    _nothing_saved(home)


@pytest.mark.parametrize(
    ("typed", "said"),
    [
        ("https://gitlab.com/acme/web", "an address"),
        ("acme/web/", "ends in '/'"),
        ("acme//web", "empty segment"),
    ],
    ids=["a pasted URL", "a trailing slash", "an empty segment"],
)
def test_a_path_that_is_an_address_or_has_a_hole_is_asked_again(
    machine, monkeypatch, capsys, typed, said
):
    home, _ = machine
    _gitlab(monkeypatch, GitLab())

    code, terminal, _ = _add(monkeypatch, ["", typed, "acme/web"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_prompts(terminal, "GitLab project path")) == 2
    assert said in captured.err and "group/project" in captured.err
    assert _row(home)["project"] == "acme/web"


def test_the_hosts_scheme_and_name_are_saved_lower_cased(machine, monkeypatch, capsys):
    home, _ = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, _, _ = _add(monkeypatch, ["HTTPS://GitLab.Example.COM/GitLab", "acme/web"])

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row(home)["host"] == "https://gitlab.example.com/GitLab", "the path as typed"
    assert provider.requests[0].url.startswith("https://gitlab.example.com/GitLab/api/v4/")


@pytest.mark.parametrize("typed", ["https://gitlab.com/api/v4", "https://git.internal/gitlab/API/v4"])
def test_a_host_ending_in_the_api_path_is_asked_again(machine, monkeypatch, capsys, typed):
    home, _ = machine
    _gitlab(monkeypatch, GitLab())

    code, terminal, _ = _add(monkeypatch, [typed, "", "acme/web"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_prompts(terminal, "GitLab host")) == 2
    assert "/api/v4" in captured.err
    assert _row(home)["host"] == DEFAULT_HOST


@pytest.mark.parametrize("typed", ["glpat-has a space", "glpat-nön-ascii"])
def test_a_typed_token_that_cannot_be_a_header_is_refused_without_a_request(
    machine, monkeypatch, capsys, typed
):
    home, _ = machine
    provider = GitLab()
    _gitlab(monkeypatch, provider)

    code, _, _ = _add(monkeypatch, VALID, token=typed)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "the token you typed holds" in captured.err
    assert "nothing was sent or saved" in captured.err
    assert "stored token" not in captured.err and "Enrol the token again" not in captured.err
    assert typed not in captured.err
    assert provider.requests == []
    _nothing_saved(home)


# ── The final save ───────────────────────────────────────────────────────────


def test_a_row_that_cannot_be_written_after_the_seal_says_which_credential_is_orphaned(
    machine, monkeypatch, capsys
):
    from pm_ai.storage.service import StorageService

    home, keychain = machine
    _gitlab(monkeypatch, GitLab())
    write = StorageService.write_artifact

    def failing(self, data, *, scope, artifact, **named):
        if artifact == "connectors/":
            raise OSError(28, "No space left on device")
        return write(self, data, scope=scope, artifact=artifact, **named)

    monkeypatch.setattr(StorageService, "write_artifact", failing)
    code, _, _ = _add(monkeypatch, VALID)
    monkeypatch.setattr(StorageService, "write_artifact", write)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "NOT enrolled" in captured.err and INSTANCE in captured.err
    _no_token_on_screen(captured)
    assert not _settings_file(home).exists()
    assert _sealed(home, keychain)[INSTANCE]["credential"] == TOKEN, "the orphan is real"


# ── Logs ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "answer",
    [GitLabResponse(200, list(ONE_COMMIT)), GitLabResponse(403, {"message": "403"})],
    ids=["success", "refusal"],
)
def test_the_token_never_reaches_a_log(machine, monkeypatch, capsys, caplog, answer):
    _gitlab(monkeypatch, GitLab(answer))
    with caplog.at_level(logging.DEBUG):
        _add(monkeypatch, VALID)
    captured = capsys.readouterr()
    _no_token_on_screen(captured)
    assert TOKEN not in caplog.text
    assert all(TOKEN not in str(record.args) for record in caplog.records)
