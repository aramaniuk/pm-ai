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
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from pm_ai.app import entry, wiring
from pm_ai.app.pipelines import run_harvest
from pm_ai.app.wiring import GraphEnrolment, bootstrap, build
from pm_ai.connectors.graph import MAX_SETTING_MINUTES, GraphConnector
from pm_ai.connectors.graph import auth as graph_auth
from pm_ai.connectors.graph.auth import (
    GRAPH_RESOURCE_SCOPES,
    GraphDeviceCodeAuth,
    RecordingHttpClient,
    SealedCredential,
)
from pm_ai.core.connector_enrolment import MalformedSettings, stored_credentials
from pm_ai.core.meeting_records import MEETINGS
from pm_ai.core.project_registry import ProjectEntry, render_registry
from pm_ai.domain.health import ArtifactState, Health
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.storage_tiers import RESTRICTED_FILE_MODE
from pm_ai.ports import KeyAlreadyEnrolled, KeyNotFound
from pm_ai.surfaces.cli import dispatch as cli
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL
from tests.connectors.recorded_graph import graph_time

INSTANCE = "graph:work"
CLIENT_ID = "00000000-0000-0000-0000-00000000008l"
ACCOUNT_CLAIMS = {"oid": "6f1c2b3a-0000-4000-8000-000000000001", "tid": "a-tenant-id"}
SIGNED_IN = "the-refresh-token-the-sign-in-returned"
ROTATED = "the-refresh-token-the-health-check-was-issued"
KEY = b"K" * 32
APPLICATION = DataScope(ScopeKind.APPLICATION)
GRANTED = " ".join(f"https://graph.microsoft.com/{s}" for s in sorted(GRAPH_RESOURCE_SCOPES))
FLOW_MESSAGE = (
    "To sign in, use a web browser to open https://microsoft.com/devicelogin "
    "and enter the code ABCD-EFGH to authenticate."
)

VALID = [CLIENT_ID, "", "1440", "10080", ""]
"""App id, Enter for the tenant, a day's window, a week's reach-back, and Enter
at `alpha`'s Outlook category question (story 4s): no category."""


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
    it raises, `accounts` is MSAL's cached account list (story 8q's fallback).
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
        accounts: list[dict] | None = None,
    ) -> None:
        self.accounts = list(accounts or [])
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
        return [dict(account) for account in self.accounts]


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
        return dataclasses.replace(
            bootstrap(chain),
            projects=entries,
            registry=ArtifactState.read(render_registry(entries)),
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
    # Four questions, then one Outlook category question for `alpha` (4s).
    assert len(terminal.prompts) == 5
    assert terminal.prompts[4] == "Outlook category that marks alpha's meetings [Enter for none]: "
    assert FLOW_MESSAGE in captured.out, "Microsoft's own address and code"
    assert "enrolled and healthy" in captured.out
    # Story 4s: the shared sentence for when it is first harvested; nothing
    # mapped, so every meeting is personal and the categories command named.
    assert cli.HARVESTED_WHEN in captured.out
    assert "every meeting is filed as personal" in captured.out
    assert f"`pm-ai connector categories {INSTANCE}`" in captured.out
    assert "nothing harvests from it" not in captured.out
    assert "until scheduled harvesting exists" not in captured.out
    assert cli.RESTART_SENTENCE not in captured.out, "nothing files into a project"
    assert str(_settings_file(home)) in captured.out, "the closing message names the file"

    row = _row(home)
    assert row["instance"] == INSTANCE and row["system"] == "graph"
    assert row["enabled"] is True
    assert row["client_id"] == CLIENT_ID
    assert row["window_width_minutes"] == 1440
    assert row["first_run_reach_back_minutes"] == 10080
    assert row["user_id"] == ACCOUNT_CLAIMS["oid"], "the signed-in user is recorded (8q)"
    assert "categories" not in row, "Enter at every category saves no key"

    assert provider.refreshed_with == [SIGNED_IN], (
        "the health check refreshed from something other than the sign-in's token"
    )
    assert _sealed_token(home, keychain) == ROTATED, (
        "the token the health check rotated to is the one sealed, not the retired one"
    )


# ── Story 8q — the row records which Microsoft user signed in ────────────────

GUEST_HOME = "99999999-8888-7777-6666-555555555555"
ROW_TENANT = "11111111-2222-3333-4444-555555555555"
"""A tenant id typed at the tenant question, so the fallback can match it."""


def _no_oid() -> dict:
    """A sign-in whose id token carries no `oid`."""
    return {**_token(SIGNED_IN), "id_token_claims": {"tid": ROW_TENANT}}


def _sealed_document(home: Path, keychain: Keychain) -> dict:
    daemon = build(home, "alpha", keychain=keychain)
    return json.loads(stored_credentials(daemon.storage)[INSTANCE]["credential"])


def test_the_sealed_sign_in_keeps_its_two_fields(machine, monkeypatch, capsys):
    """The user id lives in the row, not beside the token."""
    home, keychain = machine
    _microsoft(monkeypatch, Microsoft())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK, capsys.readouterr().err

    document = _sealed_document(home, keychain)
    assert sorted(document) == ["home_account_id", "refresh_token"]
    assert document["home_account_id"] == "6f1c2b3a-0000-4000-8000-000000000001.a-tenant-id"
    assert "user_id" not in document


def test_a_sign_in_without_oid_takes_the_one_cached_account_id(
    machine, monkeypatch, capsys
):
    home, _ = machine
    provider = Microsoft(
        device=_no_oid(), accounts=[{"home_account_id": f"the-cached-user.{ROW_TENANT}"}]
    )
    _microsoft(monkeypatch, provider)

    code, _ = _add(monkeypatch, [CLIENT_ID, ROW_TENANT, "1440", "10080", ""])

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row(home)["user_id"] == "the-cached-user"


@pytest.mark.parametrize(
    ("accounts", "said"),
    [
        ([], "0 accounts"),
        (
            [
                {"home_account_id": f"one-user.{ROW_TENANT}"},
                {"home_account_id": f"another-user.{ROW_TENANT}"},
            ],
            "2 accounts",
        ),
        ([{"home_account_id": "an-adfs-shaped-id"}], "'an-adfs-shaped-id'"),
    ],
    ids=["no account", "two accounts", "a one-part id"],
)
def test_a_sign_in_naming_no_account_is_refused_and_seals_nothing(
    machine, monkeypatch, capsys, accounts, said
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft(device=_no_oid(), accounts=accounts))

    code, _ = _add(monkeypatch, [CLIENT_ID, ROW_TENANT, "1440", "10080", ""])

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.err
    assert "Graph user id" in captured.err and "`oid`" in captured.err, (
        "the refusal names the missing identifier"
    )
    assert said in captured.err
    _nothing_saved(home)
    for token in (SIGNED_IN, ROTATED):
        assert token not in captured.out and token not in captured.err


def test_the_fallback_account_id_from_another_tenant_is_accepted(
    machine, monkeypatch, capsys
):
    """pm-ai trusts any authenticated sign-in (2026-10-12): a guest's account id
    names its home tenant, and its user part is the user id recorded."""
    home, _ = machine
    provider = Microsoft(
        device=_no_oid(),
        accounts=[{"home_account_id": f"6f1c2b3a-0000-4000-8000-000000000003.{GUEST_HOME}"}],
    )
    _microsoft(monkeypatch, provider)

    code, _ = _add(monkeypatch, [CLIENT_ID, ROW_TENANT, "1440", "10080", ""])

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row(home)["user_id"] == "6f1c2b3a-0000-4000-8000-000000000003"


@pytest.mark.parametrize(
    "tenant", ["", "contoso.onmicrosoft.com"], ids=["organizations", "a domain"]
)
def test_a_fallback_against_a_tenant_that_is_not_an_id_takes_the_one_account(
    machine, monkeypatch, capsys, tenant
):
    home, _ = machine
    provider = Microsoft(
        device=_no_oid(), accounts=[{"home_account_id": f"the-cached-user.{ROW_TENANT}"}]
    )
    _microsoft(monkeypatch, provider)

    code, _ = _add(monkeypatch, [CLIENT_ID, tenant, "1440", "10080", ""])

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row(home)["user_id"] == "the-cached-user"


@pytest.mark.parametrize("typed", ["Organizations", "COMMON"])
def test_a_tenant_word_in_any_case_is_accepted_and_stored_as_typed(
    machine, monkeypatch, capsys, typed
):
    home, _ = machine
    provider = Microsoft()
    _microsoft(monkeypatch, provider)

    code, terminal = _add(monkeypatch, [CLIENT_ID, typed, "1440", "10080", ""])

    assert code == EXIT_OK, capsys.readouterr().err
    assert len(_prompts(terminal, "Tenant")) == 1, "asked again"
    assert _row(home)["tenant"] == typed
    assert provider.authorities == [f"https://login.microsoftonline.com/{typed}"]


UNUSABLE = [
    ({"oid": "", "tid": ROW_TENANT}, "`oid` claim is blank"),
    ({"oid": "  ", "tid": ROW_TENANT}, "`oid` claim is blank"),
    ({"oid": " the-pm ", "tid": ROW_TENANT}, "padded with whitespace"),
    ({"oid": "a.b", "tid": ROW_TENANT}, "`oid` claim 'a.b' contains a `.`"),
    ({"oid": "the-pm", "tid": f"{ROW_TENANT}.extra"}, "`tid` claim"),
]
UNUSABLE_IDS = ["empty oid", "whitespace oid", "padded oid", "dotted oid", "dotted tid"]


def _with_claims(claims: dict) -> dict:
    return {**_token(SIGNED_IN), "id_token_claims": claims}


@pytest.mark.parametrize(("claims", "said"), UNUSABLE, ids=UNUSABLE_IDS)
def test_an_unusable_oid_falls_back_to_the_one_cached_account(
    machine, monkeypatch, capsys, claims, said
):
    """Never trimmed into what is sealed: the cached account is sealed as MSAL holds it."""
    home, keychain = machine
    cached = f"the-cached-user.{ROW_TENANT}"
    _microsoft(
        monkeypatch,
        Microsoft(device=_with_claims(claims), accounts=[{"home_account_id": cached}]),
    )

    code, _ = _add(monkeypatch, [CLIENT_ID, ROW_TENANT, "1440", "10080", ""])

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row(home)["user_id"] == "the-cached-user"
    assert _sealed_document(home, keychain)["home_account_id"] == cached


@pytest.mark.parametrize(("claims", "said"), UNUSABLE, ids=UNUSABLE_IDS)
def test_an_unusable_oid_with_no_cached_account_is_refused_as_unusable(
    machine, monkeypatch, capsys, claims, said
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft(device=_with_claims(claims), accounts=[]))

    code, _ = _add(monkeypatch, [CLIENT_ID, ROW_TENANT, "1440", "10080", ""])

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.err
    assert "no usable Graph user id" in captured.err, "unusable, not absent"
    assert said in captured.err
    assert "absent" not in captured.err
    _nothing_saved(home)


def test_a_sealed_credential_naming_no_user_is_refused_and_saves_nothing(
    machine, monkeypatch, capsys
):
    """The backstop after the health check: unreachable through the adapter,
    which refuses first, so reached here by making the read-back name no one."""
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())
    monkeypatch.setattr(SealedCredential, "user_id", property(lambda self: None))

    code, _ = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.err
    assert "does not name the signed-in user's Graph user id" in captured.err
    _nothing_saved(home)


def test_a_credential_that_does_not_decode_is_its_own_failure(
    machine, monkeypatch, capsys
):
    """Not folded into "names no user": the decode refusal is what is reported."""
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    class Undecodable:
        @staticmethod
        def decode(raw: str, **kwargs):
            raise wiring.GraphAuthError("the sealed Graph credential is not the document this adapter writes.")

    monkeypatch.setattr(wiring, "SealedCredential", Undecodable)

    code, _ = _add(monkeypatch, VALID)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.err
    assert "not the document this adapter writes" in captured.err
    assert "does not name the signed-in user" not in captured.err
    _nothing_saved(home)
    for token in (SIGNED_IN, ROTATED):
        assert token not in captured.err and token not in captured.out


def test_a_row_enrolled_before_8q_still_builds_without_a_word_about_it(
    machine, monkeypatch, capsys
):
    """No `user_id` in the row: the connector builds and nothing warns."""
    home, keychain = machine
    _microsoft(monkeypatch, Microsoft())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK
    row = _row(home)
    del row["user_id"]
    _settings_file(home).write_text(json.dumps(row))
    capsys.readouterr()

    daemon = build(home, "alpha", keychain=keychain)

    assert isinstance(daemon.connectors.get(INSTANCE), GraphConnector)
    captured = capsys.readouterr()
    assert "user_id" not in captured.err and "user id" not in captured.err


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


EPOCH_DATE = "Thu, 01 Jan 1970 00:00:00 GMT"


def _answering_from_the_epoch(monkeypatch):
    """Make every token answer read as sent at the epoch (story 8r).

    Skew is read off the token endpoint answer's `Date`, which the real client
    records into the adapter's `clock`. A fake MSAL has no HTTP client, so it
    records there itself: this keeps every adapter built, and returns a
    function the fake calls during its answer.
    """
    adapters: list[GraphDeviceCodeAuth] = []
    built = GraphDeviceCodeAuth.__post_init__

    def capturing(self):
        built(self)
        adapters.append(self)

    monkeypatch.setattr(GraphDeviceCodeAuth, "__post_init__", capturing)

    def answer() -> None:
        for adapter in adapters:
            adapter.clock.record({"Date": EPOCH_DATE})

    return answer


def test_a_health_check_that_warns_saves_and_says_so(machine, monkeypatch, capsys):
    """A working credential on a machine whose clock is far from Microsoft's."""
    home, keychain = machine
    from_the_epoch = _answering_from_the_epoch(monkeypatch)

    class Skewed(Microsoft):
        def acquire_token_by_refresh_token(self, refresh_token, scopes):
            # Answered at the epoch, so this machine reads as decades ahead.
            from_the_epoch()
            return super().acquire_token_by_refresh_token(refresh_token, scopes)

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

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", "120", "480", "480", ""])

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

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", "480", "60", "480", ""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    reaches = _prompts(terminal, "First-run reach-back")
    assert len(reaches) == 2
    assert "at least 480" in reaches[1]
    assert "at least 480 minutes" in captured.err
    assert _row(home)["first_run_reach_back_minutes"] == 480


# ── Story 8s — Enter at the reach-back keeps five days ───────────────────────


def test_enter_at_the_window_is_asked_again_it_keeps_no_default(machine, monkeypatch, capsys):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", "", "240", "", ""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    windows = _prompts(terminal, "Harvest window")
    assert len(windows) == 2, "Enter at the window is asked again"
    assert "no default" in windows[0] and "Enter keeps" not in windows[0]
    assert "this has no default" in captured.err
    assert _row(home)["window_width_minutes"] == 240


def test_enter_at_the_reach_back_keeps_five_days_and_the_question_names_it(
    machine, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", "240", "", ""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(terminal.prompts) == 5, "four questions and alpha's category (4s)"
    (reach,) = _prompts(terminal, "First-run reach-back")
    assert "[Enter keeps 7200]" in reach and "at least 240" in reach
    row = _row(home)
    assert (row["window_width_minutes"], row["first_run_reach_back_minutes"]) == (240, 7200)


def test_a_typed_reach_back_is_saved_as_typed(machine, monkeypatch, capsys):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, _ = _add(monkeypatch, [CLIENT_ID, "", "240", "900", ""])

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row(home)["first_run_reach_back_minutes"] == 900


def test_with_a_window_wider_than_five_days_enter_keeps_the_window(
    machine, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", "10080", "", ""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    (reach,) = _prompts(terminal, "First-run reach-back")
    assert "[Enter keeps 10080]" in reach and "7200" not in reach
    assert _row(home)["first_run_reach_back_minutes"] == 10080


def test_a_row_already_on_disk_keeps_its_reach_back(machine, monkeypatch, capsys):
    """Row: *existing Graph row* — 240 stays 240: nothing rewrites it, and the
    calendar, the transcript span and a newly listed chat or channel all read
    it."""
    home, keychain = machine
    _microsoft(monkeypatch, Microsoft())
    assert _add(monkeypatch, [CLIENT_ID, "", "240", "240", ""])[0] == EXIT_OK
    before = _settings_file(home).read_bytes()

    daemon = build(home, "alpha", keychain=keychain)

    assert _settings_file(home).read_bytes() == before, "the row was rewritten"
    connector = daemon.connectors[INSTANCE]
    assert isinstance(connector, GraphConnector), capsys.readouterr().err
    four_hours = timedelta(minutes=240)
    assert connector.calendar.windows.first_run_reach_back == four_hours
    assert connector._messages().reach_back == four_hours
    assert connector._chat_messages().reach_back == four_hours


def test_acceptance_a_first_harvest_after_enter_reaches_back_five_days(
    machine, monkeypatch, capsys
):
    """Story 8s's first acceptance criterion: a 240-minute window, Enter at the
    reach-back, then one harvest through the daemon's own `run_harvest`. The
    calendar request, read from its recorded URL, starts five days before the
    run and ends the window after it, and a meeting from four days back is
    recorded."""
    from tests.connectors.test_graph_transcripts import (
        Auth,
        FakeGraph,
        _calendar_row,
        _one_off,
    )

    home, keychain = machine
    _microsoft(monkeypatch, Microsoft())
    assert _add(monkeypatch, [CLIENT_ID, "", "240", "", ""])[0] == EXIT_OK

    now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
    four_days_ago = now - timedelta(days=4)
    graph = FakeGraph()
    graph.calendar = [_calendar_row("AAMk-four-days", four_days_ago)]
    _one_off(graph, "AAMk-four-days", start=four_days_ago)
    daemon = build(home, "alpha", keychain=keychain, now=lambda: now)
    client = daemon.connectors[INSTANCE].calendar.client
    client.transport, client.auth = graph, Auth()

    persisted = run_harvest(daemon, INSTANCE)

    assert daemon.storage.harvest_failure(INSTANCE) is None, persisted
    (asked,) = graph.asked("/me/calendarView")
    query = parse_qs(urlsplit(asked).query)
    assert graph_time(query["startDateTime"][0]) == now - timedelta(days=5)
    assert graph_time(query["endDateTime"][0]) == now + timedelta(minutes=240)
    personal = DataScope(ScopeKind.PERSONAL)
    records = daemon.storage.list_collection(scope=personal, artifact=MEETINGS)
    assert any("AAMk-four-days" in name for name in records), records


@pytest.mark.parametrize("typed", ["", "abc", "1440.0", "1_440", "+480", "١٤٤٠"])
def test_minutes_are_plain_digits_or_asked_again(machine, monkeypatch, capsys, typed):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", typed, "1440", "10080", ""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_prompts(terminal, "Harvest window")) == 2
    assert _row(home)["window_width_minutes"] == 1440


def test_a_window_past_the_cap_is_refused_in_words_for_a_typed_answer(
    machine, monkeypatch, capsys
):
    _microsoft(monkeypatch, Microsoft())
    too_wide = str(MAX_SETTING_MINUTES + 1)

    code, terminal = _add(monkeypatch, [CLIENT_ID, "", too_wide, "1440", too_wide, "10080", ""])

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

    code, _ = _add(monkeypatch, [CLIENT_ID, "contoso.onmicrosoft.com", "1440", "10080", ""])

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
        monkeypatch,
        [CLIENT_ID, typed, "11111111-2222-3333-4444-555555555555", "1440", "10080", ""],
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


def test_a_connector_built_with_no_injected_factory_uses_the_recording_client(
    machine, monkeypatch, capsys
):
    """Story 8r: production passes no factory, and must still measure the clock.

    The next start's connector — what `connector check` and `doctor` probe —
    is built by wiring without a factory, and MSAL is replaced at its import
    so nothing reaches the network. It must hand MSAL the recording client,
    bound to that adapter's own clock.
    """
    _, keychain = machine
    _microsoft(monkeypatch, Microsoft())
    assert _add(monkeypatch, VALID)[0] == EXIT_OK

    built: list[dict] = []

    class Msal:
        @staticmethod
        def PublicClientApplication(client_id, *, authority, http_client):
            built.append({"client_id": client_id, "http_client": http_client})
            return Microsoft()

    monkeypatch.setattr(graph_auth, "require_msal", lambda: Msal)

    composed = entry._compose(keychain, APPLICATION)
    assert composed.daemon is not None
    connector = composed.daemon.connectors.get(INSTANCE)
    assert isinstance(connector, GraphConnector), capsys.readouterr().err
    connector.auth._application()

    (made,) = built
    assert made["client_id"] == CLIENT_ID
    client = made["http_client"]
    assert isinstance(client, RecordingHttpClient)
    assert client.clock is connector.auth.clock
    assert client.session.trust_env is True


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


# ── Story 4s — Outlook categories are asked, not hand-edited ─────────────────

FOUR = [CLIENT_ID, "", "1440", "10080"]
"""The four questions' answers, without any category answer."""


def _enrolled(
    monkeypatch,
    tmp_path: Path,
    projects: tuple[str, ...],
    *,
    later: ArtifactState | None = None,
    once: Callable[[], bool] = lambda: False,
) -> None:
    """The machine's registry: these project ids, in this order in the file.

    Written by hand rather than by `render_registry`, which sorts, so the
    order is the file's. `later` is what the registry reads as once `once()`
    is true — composition has read it whole by then.
    """
    entries = {}
    lines = []
    for project in projects:
        repository = tmp_path / f"repo-{project}"
        repository.mkdir(exist_ok=True)
        entries[project] = ProjectEntry(path=repository)
        lines.append(f'[projects.{project}]\npath = "{repository}"\n')
    state = ArtifactState.read("\n".join(lines).encode()) if entries else ArtifactState.absent()

    def read(chain):
        now = later if later is not None and once() else state
        return dataclasses.replace(bootstrap(chain), projects=entries, registry=now)

    monkeypatch.setattr(entry, "_bootstrap", read)


def _category_prompts(terminal: Terminal) -> list[str]:
    return _prompts(terminal, "Outlook category")


def test_a_mapped_project_is_saved_in_the_same_row_and_said_with_the_restart(
    machine, monkeypatch, capsys
):
    """Rows *a project mapped* and *one project, a category*: ` Alpha ` trimmed."""
    home, keychain = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [*FOUR, " Alpha "])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert _category_prompts(terminal) == [
        "Outlook category that marks alpha's meetings [Enter for none]: "
    ]
    row = _row(home)
    assert row["categories"] == {"Alpha": "alpha"}
    assert row["user_id"] == ACCOUNT_CLAIMS["oid"], "saved in the same write as the rest"
    assert cli.HARVESTED_WHEN in captured.out
    assert "'Alpha' into alpha" in captured.out
    assert "Every other meeting is filed as personal" in captured.out
    assert f"`pm-ai connector categories {INSTANCE}`" in captured.out
    clause = cli.PROJECT_CLAUSE.format(instance=INSTANCE, projects="alpha", which="alpha")
    assert f"{clause} {cli.RESTART_SENTENCE}" in captured.out
    # The connector the next start builds files an `Alpha` meeting into alpha.
    daemon = build(home, "alpha", keychain=keychain)
    assert isinstance(daemon.connectors.get(INSTANCE), GraphConnector)


def test_the_questions_come_after_the_four_and_before_the_sign_in(
    machine, monkeypatch, capsys
):
    _microsoft(monkeypatch, Microsoft())
    asked_at_sign_in: list[int] = []
    real = GraphEnrolment.sign_in

    def sign_in(self, **kwargs):
        asked_at_sign_in.append(len(terminal.prompts))
        return real(self, **kwargs)

    monkeypatch.setattr(GraphEnrolment, "sign_in", sign_in)
    terminal = Terminal(VALID)
    monkeypatch.setattr("builtins.input", terminal)

    assert entry.main(["connector", "add", "graph", INSTANCE]) == EXIT_OK, capsys.readouterr().err
    assert terminal.prompts[4].startswith("Outlook category that marks alpha's")
    assert asked_at_sign_in == [5], "every question was asked before the sign-in"


def test_projects_are_asked_in_the_registrys_order(machine, tmp_path, monkeypatch, capsys):
    home, _ = machine
    _enrolled(monkeypatch, tmp_path, ("beta", "alpha", "gamma"))
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [*FOUR, "B", "", "G"])

    assert code == EXIT_OK, capsys.readouterr().err
    assert [p.split("marks ")[1].split("'")[0] for p in _category_prompts(terminal)] == [
        "beta",
        "alpha",
        "gamma",
    ]
    assert _row(home)["categories"] == {"B": "beta", "G": "gamma"}


def test_one_category_for_two_projects_is_refused_naming_both_and_the_later_asked_again(
    machine, tmp_path, monkeypatch, capsys
):
    home, _ = machine
    _enrolled(monkeypatch, tmp_path, ("alpha", "beta"))
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [*FOUR, "Alpha", "alpha", "Beta"])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    prompts = _category_prompts(terminal)
    assert len(prompts) == 3
    assert "alpha's" in prompts[0] and "beta's" in prompts[1] and "beta's" in prompts[2]
    assert "'alpha' for beta is the same Outlook category as 'Alpha' for alpha" in captured.err
    assert _row(home)["categories"] == {"Alpha": "alpha", "Beta": "beta"}


def test_a_dash_where_nothing_can_be_removed_is_refused_and_asked_again(
    machine, monkeypatch, capsys
):
    home, _ = machine
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, [*FOUR, "-", ""])

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(_category_prompts(terminal)) == 2
    assert "there is nothing to remove; press Enter for none" in captured.err
    assert "- removes" not in _category_prompts(terminal)[0], "`-` is not offered"
    assert "categories" not in _row(home), "no category is ever saved as `-`"


def test_with_no_project_enrolled_no_category_is_asked(machine, tmp_path, monkeypatch, capsys):
    home, _ = machine
    _enrolled(monkeypatch, tmp_path, ())
    _microsoft(monkeypatch, Microsoft())

    code, terminal = _add(monkeypatch, FOUR)

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert len(terminal.prompts) == 4 and not _category_prompts(terminal)
    assert "categories" not in _row(home)
    assert "every meeting is filed as personal" in captured.out


@pytest.mark.parametrize("interruption", [EOFError(), KeyboardInterrupt()], ids=["ctrl-d", "ctrl-c"])
def test_an_interruption_at_a_category_question_saves_nothing(
    machine, monkeypatch, capsys, interruption
):
    home, _ = machine
    provider = Microsoft()
    _microsoft(monkeypatch, provider)

    code, _ = _add(monkeypatch, [*FOUR, interruption])

    assert code == EXIT_REFUSAL
    assert "nothing was saved" in capsys.readouterr().err
    assert provider.authorities == [], "no sign-in was started"
    _nothing_saved(home)


@pytest.mark.parametrize(
    "registry",
    [ArtifactState.unreadable("Permission denied"), ArtifactState.read(b"[projects\n")],
    ids=["unreadable", "malformed"],
)
def test_a_registry_that_cannot_be_read_is_a_refusal_that_saves_nothing(
    machine, tmp_path, monkeypatch, capsys, registry
):
    """Readable when the command composed; unreadable when the questions start."""
    home, _ = machine
    terminal = Terminal([*FOUR, ""])
    _enrolled(
        monkeypatch, tmp_path, ("alpha",), later=registry, once=lambda: len(terminal.prompts) >= 4
    )
    provider = Microsoft()
    _microsoft(monkeypatch, provider)
    monkeypatch.setattr("builtins.input", terminal)

    code = entry.main(["connector", "add", "graph", INSTANCE])

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "projects.toml" in captured.err and "Nothing was saved" in captured.err
    assert not _category_prompts(terminal)
    assert provider.authorities == []
    _nothing_saved(home)
