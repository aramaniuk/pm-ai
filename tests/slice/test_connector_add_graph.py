"""Story 8l — `pm-ai connector add graph <name>`, one test per matrix row.

Every row runs `entry.main` against a real temporary home, so the sealed
credentials file, its encryption, its claim and the `connectors/` row are the
real ones. Only two things are faked: the keychain, and Microsoft — the
adapter's MSAL client, injected through `GraphEnrolment.client_factory` the way
`tests/connectors/test_graph_auth.py` injects it. No test opens a socket.

The questions are answered by replacing `input`, which also records every
prompt, so "asked again" and "refused before any question" are assertions about
what was asked rather than about what was printed.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import stat
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pm_ai.app import entry, wiring
from pm_ai.app.wiring import Bootstrap, GraphEnrolment, bootstrap, build
from pm_ai.connectors.graph import MAX_SETTING_MINUTES, GraphConnector
from pm_ai.connectors.graph.auth import (
    GRAPH_RESOURCE_SCOPES,
    GraphDeviceCodeAuth,
    SealedCredential,
)
from pm_ai.core.connector_enrolment import MalformedSettings, stored_credentials
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.domain.health import ArtifactState, Health
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.storage_tiers import RESTRICTED_FILE_MODE
from pm_ai.ports import KeyAlreadyEnrolled, KeyNotFound
from pm_ai.surfaces.cli import dispatch as cli
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL

INSTANCE = "graph:work"
CLIENT_ID = "00000000-0000-0000-0000-00000000008l"
ACCOUNT_CLAIMS = {"oid": "an-object-id", "tid": "a-tenant-id"}
SIGNED_IN = "the-refresh-token-the-sign-in-returned"
ROTATED = "the-refresh-token-the-health-check-was-issued"
KEY = b"K" * 32
APPLICATION = DataScope(ScopeKind.APPLICATION)
GRANTED = " ".join(f"https://graph.microsoft.com/{s}" for s in sorted(GRAPH_RESOURCE_SCOPES))
FLOW_MESSAGE = (
    "To sign in, use a web browser to open https://microsoft.com/devicelogin "
    "and enter the code ABCD-EFGH to authenticate."
)

VALID = [CLIENT_ID, "", "1440", "10080"]
"""App id, Enter for the tenant, a day's window, a week's reach-back."""


def _token(refresh_token: str, **claims: Any) -> dict:
    return {
        "access_token": "an-access-token",
        "refresh_token": refresh_token,
        "expires_in": 3600,
        "scope": GRANTED,
        "token_type": "Bearer",
        "id_token_claims": {**ACCOUNT_CLAIMS, **claims},
    }


class Microsoft:
    """MSAL's `PublicClientApplication`, as much as the sign-in and the check touch.

    One refresh token is live at a time, as at Microsoft: the device flow
    issues `SIGNED_IN` (or whatever `device` carries), and redeeming the live
    token issues `issue` and retires the one redeemed. A token that is not live
    is refused, so a check that refreshed from the wrong token fails.
    `refreshed_with` records every token redeemed.

    The other knobs make one step misbehave: `build` raises at construction,
    `start` is what asking for a code answers or raises, `hold_start` and
    `hold_refresh` make a step wait, `device` is the sign-in's answer or what
    it raises.
    """

    def __init__(
        self,
        *,
        live: str | None = None,
        device: dict | BaseException | None = None,
        issue: str = ROTATED,
        refresh: dict | None = None,
        build: BaseException | None = None,
        start: dict | BaseException | None = None,
        hold_start: threading.Event | None = None,
        hold_refresh: threading.Event | None = None,
    ) -> None:
        self.live = live
        self.device = device if device is not None else _token(SIGNED_IN)
        self.issue = issue
        self.refresh = refresh
        self.build = build
        self.start = start
        self.hold_start = hold_start
        self.hold_refresh = hold_refresh
        self.authorities: list[str] = []
        self.refreshed_with: list[str] = []
        self.shown_code = False

    def __call__(self, client_id: str, authority: str) -> "Microsoft":
        self.authorities.append(authority)
        if self.build is not None:
            raise self.build
        return self

    def initiate_device_flow(self, scopes):
        if self.hold_start is not None:
            self.hold_start.wait(30)
        if isinstance(self.start, BaseException):
            raise self.start
        if self.start is not None:
            return dict(self.start)
        return {
            "user_code": "ABCD-EFGH",
            "device_code": "a-device-code",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
            "message": FLOW_MESSAGE,
        }

    def acquire_token_by_device_flow(self, flow):
        if isinstance(self.device, BaseException):
            raise self.device
        answer = dict(self.device)
        if "refresh_token" in answer:
            self.live = answer["refresh_token"]
        return answer

    def acquire_token_by_refresh_token(self, refresh_token, scopes):
        self.refreshed_with.append(refresh_token)
        if self.hold_refresh is not None:
            self.hold_refresh.wait(30)
        if self.refresh is not None:
            return dict(self.refresh)
        if refresh_token != self.live:
            return {"error": "invalid_grant", "error_description": "AADSTS70000: retired"}
        self.live = self.issue
        return _token(self.issue)

    def acquire_token_silent(self, scopes, account, force_refresh=False):
        return None

    def get_accounts(self, username=None):
        return []


class Keychain:
    """One master key, already enrolled unless `secret=None`."""

    def __init__(self, secret: bytes | None = KEY) -> None:
        self.secret = secret

    def store(self, name: str, secret: bytes) -> None:
        self.secret = secret

    def store_if_absent(self, name: str, secret: bytes) -> None:
        if self.secret is not None:
            raise KeyAlreadyEnrolled(name)
        self.secret = secret

    def fetch(self, name: str) -> bytes:
        if self.secret is None:
            raise KeyNotFound(name)
        return self.secret

    def delete(self, name: str) -> None:
        self.secret = None


class Terminal:
    """`input`, answering from a script and recording every prompt it was shown.

    An answer that is an exception is raised instead — Ctrl-D or Ctrl-C at
    that prompt. Running out of answers is Ctrl-D.
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


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """One enrolled project, a master key, a terminal, and an empty home."""
    keychain = Keychain()
    monkeypatch.setattr(entry, "MacOSKeychainAdapter", lambda: keychain)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repository = tmp_path / "repo"
    repository.mkdir()
    entries = {"alpha": ProjectEntry(path=repository)}

    def read(chain):
        return Bootstrap(
            entries,
            ArtifactState.read(render_registry(entries)),
            bootstrap(chain).config,
        )

    monkeypatch.setattr(entry, "_bootstrap", read)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    return home, keychain


def _microsoft(monkeypatch, provider: Microsoft | None, *, timeout: float = 10.0) -> None:
    """Make the composition root's `GraphEnrolment` talk to `provider`."""
    monkeypatch.setattr(
        entry,
        "GraphEnrolment",
        functools.partial(GraphEnrolment, client_factory=provider, timeout=timeout),
    )


def _add(monkeypatch, answers: list) -> tuple[int, Terminal]:
    terminal = Terminal(answers)
    monkeypatch.setattr("builtins.input", terminal)
    return entry.main(["connector", "add", "graph", INSTANCE]), terminal


def _settings_file(home: Path) -> Path:
    return home / ".pm-ai" / "connectors" / f"{INSTANCE}.json"


def _row(home: Path) -> dict:
    return json.loads(_settings_file(home).read_text())


def _sealed_token(home: Path, keychain: Keychain) -> str:
    daemon = build(home, "alpha", keychain=keychain)
    sealed = stored_credentials(daemon.storage)[INSTANCE]
    assert sealed["system"] == "graph"
    return SealedCredential.decode(sealed["credential"]).refresh_token


def _nothing_saved(home: Path) -> None:
    connectors = home / ".pm-ai" / "connectors"
    assert not connectors.exists() or not any(connectors.iterdir()), "a row was written"
    assert not (home / ".pm-ai" / "private" / "config.json").exists(), (
        "a credential was sealed"
    )


def _prompts(terminal: Terminal, starting: str) -> list[str]:
    return [p for p in terminal.prompts if p.startswith(starting)]


# ── Happy path ───────────────────────────────────────────────────────────────


def test_a_valid_add_saves_the_settings_and_the_sealed_sign_in_together(
    machine, monkeypatch, capsys
):
    home, keychain = machine
    provider = Microsoft()
    _microsoft(monkeypatch, provider)

    code, terminal = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(terminal.prompts) == 4
    assert FLOW_MESSAGE in captured.out, "Microsoft's own address and code"
    assert "enrolled and healthy" in captured.out
    assert "next start" in captured.out
    assert str(_settings_file(home)) in captured.out, "the closing message names the file"
    assert "categories" in captured.out

    row = _row(home)
    assert row["instance"] == INSTANCE and row["system"] == "graph"
    assert row["enabled"] is True
    assert row["client_id"] == CLIENT_ID
    assert row["window_width_minutes"] == 1440
    assert row["first_run_reach_back_minutes"] == 10080
    assert "categories" not in row, "category mapping is not asked"

    assert provider.refreshed_with == [SIGNED_IN], (
        "the health check refreshed from something other than the sign-in's token"
    )
    assert _sealed_token(home, keychain) == ROTATED, (
        "the token the health check rotated to is the one sealed, not the retired one"
    )


def test_the_settings_file_is_written_at_0600(machine, monkeypatch, capsys):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK, capsys.readouterr().err
    assert stat.S_IMODE(_settings_file(home).stat().st_mode) == RESTRICTED_FILE_MODE == 0o600


def test_the_sign_in_token_appears_nowhere_on_screen(machine, monkeypatch, capsys):
    _microsoft(monkeypatch, Microsoft())
    code, _ = _add(monkeypatch, VALID)
    captured = capsys.readouterr()
    assert code == EXIT_OK
    for token in (SIGNED_IN, ROTATED, "an-access-token"):
        assert token not in captured.out
        assert token not in captured.err


def test_a_failure_quoting_the_sign_in_token_does_not_print_it(
    machine, monkeypatch, capsys
):
    """Microsoft's text is checked for the raw refresh token, not only for the sealed blob."""
    home, _ = machine
    quoting = {"error": "invalid_grant", "error_description": f"AADSTS70000: {SIGNED_IN}"}
    _microsoft(monkeypatch, Microsoft(refresh=quoting))

    code, _ = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert SIGNED_IN not in captured.err and SIGNED_IN not in captured.out
    assert SIGNED_IN[:12] not in captured.err
    _nothing_saved(home)


def test_a_health_check_that_warns_saves_and_says_so(machine, monkeypatch, capsys):
    """A working credential on a machine whose clock is far from Microsoft's."""
    home, keychain = machine

    class Skewed(Microsoft):
        def acquire_token_by_refresh_token(self, refresh_token, scopes):
            answer = super().acquire_token_by_refresh_token(refresh_token, scopes)
            # Minted at the epoch, so this machine reads as decades ahead.
            answer["id_token_claims"] = {**ACCOUNT_CLAIMS, "iat": 0}
            return answer

    _microsoft(monkeypatch, Skewed())

    code, _ = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "health check warned" in captured.out
    assert "Fix the clock" in captured.out
    assert "enrolled and healthy" not in captured.out
    assert _row(home)["client_id"] == CLIENT_ID
    assert _sealed_token(home, keychain) == ROTATED


# ── Refused before any question ──────────────────────────────────────────────


def test_a_name_in_use_is_refused_before_any_question(machine, monkeypatch, capsys):
    _microsoft(monkeypatch, Microsoft())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK
    capsys.readouterr()

    code, terminal = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    assert terminal.prompts == [], "a question was asked before the name was checked"
    assert "already configured" in capsys.readouterr().err


def test_without_a_terminal_nothing_is_asked(machine, monkeypatch, capsys):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code, terminal = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    assert terminal.prompts == []
    assert "terminal" in capsys.readouterr().err
    _nothing_saved(home)


def test_a_machine_with_no_master_key_is_refused_before_any_question(
    machine, monkeypatch, capsys
):
    home, keychain = machine
    keychain.secret = None
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    assert terminal.prompts == [], "questions were asked of a sign-in that could not be sealed"
    assert "pm-ai key enrol" in capsys.readouterr().err
    _nothing_saved(home)


def test_missing_runtime_packages_name_the_extra_before_any_question(
    machine, monkeypatch, capsys
):
    home, _ = machine
    # The adapter's own client factory, with `msal` made unimportable whether
    # or not this environment happens to have it.
    _microsoft(monkeypatch, None)
    monkeypatch.setitem(sys.modules, "msal", None)

    code, terminal = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    assert terminal.prompts == []
    assert "uv sync --extra runtime" in capsys.readouterr().err
    _nothing_saved(home)


# ── Answers that would not build a connector ─────────────────────────────────


def test_a_window_under_240_is_asked_again_naming_the_minimum(
    machine, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", "120", "480", "480"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    windows = _prompts(terminal, "Harvest window")
    assert len(windows) == 2, "the window was not asked again"
    assert "240" in windows[0], "the question states the minimum"
    assert "240-minute minimum" in captured.err
    assert _row(home)["window_width_minutes"] == 480


def test_a_reach_back_under_the_window_is_asked_again(machine, monkeypatch, capsys):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", "480", "60", "480"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    reaches = _prompts(terminal, "First-run reach-back")
    assert len(reaches) == 2
    assert "at least 480" in reaches[1]
    assert "at least 480 minutes" in captured.err
    assert _row(home)["first_run_reach_back_minutes"] == 480


@pytest.mark.parametrize("typed", ["", "abc", "1440.0", "1_440", "+480", "١٤٤٠"])
def test_minutes_are_plain_digits_or_asked_again(machine, monkeypatch, capsys, typed):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", typed, "1440", "10080"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_prompts(terminal, "Harvest window")) == 2
    assert _row(home)["window_width_minutes"] == 1440


def test_a_window_past_the_cap_is_refused_in_words_for_a_typed_answer(
    machine, monkeypatch, capsys
):
    _microsoft(monkeypatch, Microsoft())
    too_wide = str(MAX_SETTING_MINUTES + 1)

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", too_wide, "1440", too_wide, "10080"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_prompts(terminal, "Harvest window")) == 2
    assert len(_prompts(terminal, "First-run reach-back")) == 2
    assert str(MAX_SETTING_MINUTES) in captured.err
    assert "row" not in captured.err and "hand-edit" not in captured.err


def test_a_blank_client_id_is_asked_again(machine, monkeypatch, capsys):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, ["", *VALID])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_prompts(terminal, "Microsoft app (client) id")) == 2
    assert "no app (client) id" in captured.err
    assert _row(home)["client_id"] == CLIENT_ID


def test_a_blank_tenant_saves_organizations(machine, monkeypatch, capsys):
    home, _ = machine
    provider = Microsoft()
    _microsoft(monkeypatch, provider)

    code, _ = _add(monkeypatch, VALID)

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row(home)["tenant"] == "organizations"
    assert provider.authorities == ["https://login.microsoftonline.com/organizations"]


def test_a_typed_tenant_reaches_the_sign_in_and_the_row(machine, monkeypatch, capsys):
    home, _ = machine
    provider = Microsoft()
    _microsoft(monkeypatch, provider)

    code, _ = _add(monkeypatch, [CLIENT_ID, "contoso.onmicrosoft.com", "1440", "10080"])

    assert code == EXIT_OK, capsys.readouterr().err
    assert provider.authorities == ["https://login.microsoftonline.com/contoso.onmicrosoft.com"]
    assert _row(home)["tenant"] == "contoso.onmicrosoft.com"


@pytest.mark.parametrize(
    "typed",
    ["contoso com", "contoso/evil", "https://login.example.com/x", "contoso", "-bad-.com"],
)
def test_a_tenant_that_is_not_one_is_asked_again(machine, monkeypatch, capsys, typed):
    home, _ = machine
    provider = Microsoft()
    _microsoft(monkeypatch, provider)

    code, terminal = _add(
        monkeypatch, [CLIENT_ID, typed, "11111111-2222-3333-4444-555555555555", "1440", "10080"]
    )

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_prompts(terminal, "Tenant")) == 2
    assert "not a tenant Microsoft can sign in through" in captured.err
    assert _row(home)["tenant"] == "11111111-2222-3333-4444-555555555555"


# ── Interrupted ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("interruption", [EOFError(), KeyboardInterrupt()])
def test_an_interruption_at_a_question_saves_nothing(
    machine, monkeypatch, capsys, interruption
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, _ = _add(monkeypatch, [CLIENT_ID, "", interruption])

    assert code == EXIT_REFUSAL
    assert "nothing was saved" in capsys.readouterr().err
    _nothing_saved(home)


def test_ctrl_c_during_the_sign_in_exits_3_and_saves_nothing(
    machine, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft(device=KeyboardInterrupt()))

    code, _ = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    assert "interrupted" in capsys.readouterr().err
    _nothing_saved(home)


# ── The sign-in and the check refuse ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("answered", "reason"),
    [
        ({"error": "access_denied", "error_description": "the user declined"}, "declined"),
        ({"error": "expired_token", "error_description": "code expired"}, "expired"),
    ],
)
def test_a_declined_or_expired_sign_in_saves_nothing(
    machine, monkeypatch, capsys, answered, reason
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft(device=answered))

    code, _ = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    assert reason in capsys.readouterr().err
    _nothing_saved(home)


def test_a_partial_consent_saves_nothing(machine, monkeypatch, capsys):
    home, _ = machine
    partial = _token(SIGNED_IN)
    partial["scope"] = "https://graph.microsoft.com/Calendars.Read"
    _microsoft(monkeypatch, Microsoft(device=partial))

    code, _ = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    assert "Chat.Read" in capsys.readouterr().err, "the missing permission is named"
    _nothing_saved(home)


def test_microsoft_unreachable_during_the_sign_in_saves_nothing(
    machine, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft(device=OSError("no route to host")))

    code, _ = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    assert "not the credential" in capsys.readouterr().err
    _nothing_saved(home)


def test_a_tenant_microsoft_refuses_at_construction_is_named_not_blamed_on_the_network(
    machine, monkeypatch, capsys
):
    home, _ = machine
    refused = ValueError("Unable to get authority configuration for the tenant")
    _microsoft(monkeypatch, Microsoft(build=refused))

    code, _ = _add(monkeypatch, VALID)

    err = capsys.readouterr().err
    assert code == EXIT_REFUSAL
    assert "tenant 'organizations'" in err
    assert "not the network" in err
    assert "not the credential" not in err
    _nothing_saved(home)


def test_an_app_id_microsoft_does_not_know_is_named(machine, monkeypatch, capsys):
    home, _ = machine
    unknown = {
        "error": "unauthorized_client",
        "error_description": "AADSTS700016: Application with identifier was not found",
    }
    _microsoft(monkeypatch, Microsoft(start=unknown))

    code, _ = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "app (client) id" in captured.err and CLIENT_ID in captured.err
    assert "not the credential" not in captured.err
    assert FLOW_MESSAGE not in captured.out
    _nothing_saved(home)


def test_a_transport_failure_building_the_client_stays_unreachable(
    machine, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft(build=OSError("no route to the authority")))

    code, _ = _add(monkeypatch, VALID)

    assert code == EXIT_REFUSAL
    assert "not the credential" in capsys.readouterr().err
    _nothing_saved(home)


def test_asking_for_a_code_is_bounded(machine, monkeypatch, capsys):
    home, _ = machine
    hold = threading.Event()
    _microsoft(monkeypatch, Microsoft(hold_start=hold), timeout=0.2)
    try:
        code, _ = _add(monkeypatch, VALID)
    finally:
        hold.set()

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "did not issue a sign-in code within 0.2s" in captured.err
    assert FLOW_MESSAGE not in captured.out
    _nothing_saved(home)


def test_a_failing_health_check_saves_nothing(machine, monkeypatch, capsys):
    home, _ = machine
    rejected = {"error": "invalid_grant", "error_description": "AADSTS70000: revoked"}
    _microsoft(monkeypatch, Microsoft(refresh=rejected))

    code, _ = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "health check failed" in captured.err
    _nothing_saved(home)


def test_a_health_check_past_its_bound_saves_nothing(machine, monkeypatch, capsys):
    home, _ = machine
    hold = threading.Event()
    _microsoft(monkeypatch, Microsoft(hold_refresh=hold), timeout=0.2)
    try:
        code, _ = _add(monkeypatch, VALID)
    finally:
        hold.set()

    assert code == EXIT_REFUSAL
    assert "did not answer within 0.2s" in capsys.readouterr().err
    _nothing_saved(home)


# ── Saving ───────────────────────────────────────────────────────────────────


def test_settings_enrolment_refuses_are_a_refusal_not_a_traceback(
    machine, monkeypatch, capsys
):
    _microsoft(monkeypatch, Microsoft())

    def refusing(*args, **kwargs):
        raise MalformedSettings("the settings for 'graph:work' name system")

    monkeypatch.setattr(cli, "enrol_connector", refusing)
    code, _ = _add(monkeypatch, VALID)
    assert code == EXIT_REFUSAL
    assert "name system" in capsys.readouterr().err


# ── The connector it adds is the one the next start builds ───────────────────


def _faked_auth(provider: Callable[[str, str], Any]) -> type[GraphDeviceCodeAuth]:
    """`GraphDeviceCodeAuth` whose client is `provider` unless one is passed."""

    @dataclasses.dataclass
    class Faked(GraphDeviceCodeAuth):
        client_factory: Callable[[str, str], Any] = provider

    return Faked


def test_the_added_connector_is_built_at_the_next_start(machine, monkeypatch, capsys):
    _, keychain = machine
    _microsoft(monkeypatch, Microsoft())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK

    composed = entry._compose(keychain, APPLICATION)
    assert composed.daemon is not None
    connector = composed.daemon.connectors.get(INSTANCE)
    assert isinstance(connector, GraphConnector), capsys.readouterr().err

    # The next start refreshes from the sealed token, which is the rotated one.
    next_start = Microsoft(live=ROTATED, issue=ROTATED)
    connector.auth.client_factory = next_start
    probe = connector.check_health()
    assert probe.health is Health.OK, probe.detail
    assert next_start.refreshed_with == [ROTATED]


def test_connector_check_lists_the_added_connector_as_healthy(
    machine, monkeypatch, capsys
):
    _microsoft(monkeypatch, Microsoft())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK
    capsys.readouterr()

    monkeypatch.setattr(
        wiring, "GraphDeviceCodeAuth", _faked_auth(Microsoft(live=ROTATED, issue=ROTATED))
    )
    entry.main(["connector", "check"])

    # Not the exit code: the project's built-in GitLab connector holds no
    # credential here and answers ABSENT, which is its own row.
    out = capsys.readouterr().out
    (line,) = [line for line in out.splitlines() if f"] {INSTANCE}:" in line]
    assert f"[{Health.OK.value:>7}]" in line, out


def test_a_hand_edited_tenant_that_is_not_one_builds_no_connector(
    machine, monkeypatch, capsys
):
    """The check the typed answer meets is applied to the row at the next start."""
    home, keychain = machine
    _microsoft(monkeypatch, Microsoft())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK
    row = _row(home)
    row["tenant"] = "contoso/evil"
    _settings_file(home).write_text(json.dumps(row))
    capsys.readouterr()

    daemon = build(home, "alpha", keychain=keychain)

    assert INSTANCE not in daemon.connectors
    assert "not a tenant Microsoft can sign in through" in capsys.readouterr().err
