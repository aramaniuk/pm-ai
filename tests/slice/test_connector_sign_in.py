"""Story 8m — `pm-ai connector sign-in <instance>`, one test per matrix row.

Every row runs `entry.main` against a real temporary home, as story 8l's do:
the sealed credentials file, its encryption, its claim and the `connectors/`
row are the real ones, and only the keychain and Microsoft are faked. The
connector being signed in is enrolled first through `pm-ai connector add graph`
itself, so the row and the sealed entry are exactly what that command writes.

"Untouched" is asserted on bytes: the sealed file is re-encrypted on every
write, so a rewrite that changed nothing still changes its bytes.
"""

from __future__ import annotations

import functools
import json
import os
import sys
from pathlib import Path

import pytest

from claim_fakes import held_by_another_thread
from pm_ai.app import entry, wiring
from pm_ai.app.wiring import Bootstrap, GraphEnrolment, bootstrap, build
from pm_ai.connectors.graph import GraphConnector
from pm_ai.core.connector_enrolment import stored_credentials
from pm_ai.domain.health import ArtifactState, Health
from pm_ai.surfaces.cli import dispatch as cli
from pm_ai.surfaces.cli.dispatch import EXIT_OK, EXIT_REFUSAL
from test_connector_add_graph import (  # noqa: F401 — `machine` is a fixture
    ACCOUNT_CLAIMS,
    APPLICATION,
    CLIENT_ID,
    FLOW_MESSAGE,
    INSTANCE,
    ROTATED,
    SIGNED_IN,
    VALID,
    Microsoft,
    Terminal,
    _faked_auth,
    _token,
    machine,
)

NEW_SIGNED_IN = "the-refresh-token-the-new-sign-in-returned"
NEW_ROTATED = "the-refresh-token-the-new-check-was-issued"
OTHER = "graph:home"
"""A second enrolled connector, which a sign-in for `INSTANCE` must not touch."""
ROW_TENANT = "11111111-2222-3333-4444-555555555555"
OTHER_TENANT = "99999999-8888-7777-6666-555555555555"
TOKENS = (SIGNED_IN, ROTATED, NEW_SIGNED_IN, NEW_ROTATED, "an-access-token")
COMMAND = f"`pm-ai connector sign-in {INSTANCE}`"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _microsoft(monkeypatch, provider: Microsoft | None, *, timeout: float = 10.0) -> None:
    """`GraphEnrolment` talking to `provider`, never sleeping for the claim."""
    monkeypatch.setattr(
        entry,
        "GraphEnrolment",
        functools.partial(
            GraphEnrolment,
            client_factory=provider,
            timeout=timeout,
            claim_sleep=lambda seconds: None,
        ),
    )


def _add(monkeypatch, instance: str = INSTANCE, answers: list | None = None) -> None:
    """Enrol `instance` through `pm-ai connector add graph`, as a PM would."""
    _microsoft(monkeypatch, Microsoft())
    monkeypatch.setattr("builtins.input", Terminal(list(answers or VALID)))
    assert entry.main(["connector", "add", "graph", instance]) == EXIT_OK


def _provider(**claims) -> Microsoft:
    """Microsoft for the new sign-in: a fresh token, rotated by the check."""
    return Microsoft(device=_token(NEW_SIGNED_IN, **claims), issue=NEW_ROTATED)


def _sign_in(monkeypatch, provider: Microsoft | None, instance: str = INSTANCE, **kw):
    """Run `pm-ai connector sign-in`; return the exit code and the terminal."""
    _microsoft(monkeypatch, provider, **kw)
    terminal = Terminal([])
    monkeypatch.setattr("builtins.input", terminal)
    return entry.main(["connector", "sign-in", instance]), terminal


def _row_file(home: Path, instance: str = INSTANCE) -> Path:
    return home / ".pm-ai" / "connectors" / f"{instance}.json"


def _row(home: Path, instance: str = INSTANCE) -> dict:
    return json.loads(_row_file(home, instance).read_text())


def _write_row(home: Path, row: dict, instance: str = INSTANCE) -> None:
    _row_file(home, instance).write_text(json.dumps(row))


def _sealed_file(home: Path) -> Path:
    return home / ".pm-ai" / "private" / "config.json"


def _state(home: Path) -> tuple[bytes | None, dict[str, bytes]]:
    """The sealed file's bytes and every row's bytes, for "untouched"."""
    sealed = _sealed_file(home)
    rows = home / ".pm-ai" / "connectors"
    return (
        sealed.read_bytes() if sealed.exists() else None,
        {p.name: p.read_bytes() for p in sorted(rows.glob("*.json"))} if rows.exists() else {},
    )


def _sealed(home: Path, keychain) -> dict[str, dict[str, str]]:
    return stored_credentials(build(home, "alpha", keychain=keychain).storage)


def _token_of(home: Path, keychain, instance: str = INSTANCE) -> str:
    sealed = _sealed(home, keychain)[instance]
    assert sealed["system"] == "graph"
    return json.loads(sealed["credential"])["refresh_token"]


def _no_token_printed(captured) -> None:
    for token in TOKENS:
        assert token not in captured.out and token not in captured.err, token
        assert token[:12] not in captured.out and token[:12] not in captured.err


def _rewrite_sealed(home: Path, keychain, change) -> None:
    """Edit the decrypted sealed document through the single writer."""
    storage = build(home, "alpha", keychain=keychain).storage
    document = json.loads(storage.read_artifact(scope=APPLICATION, artifact="config.json"))
    change(document)
    storage.write_artifact(json.dumps(document).encode(), scope=APPLICATION, artifact="config.json")


@pytest.fixture
def enrolled(machine, monkeypatch, capsys):
    """`INSTANCE` and `OTHER` enrolled through `connector add graph`."""
    home, keychain = machine
    _add(monkeypatch)
    _add(monkeypatch, OTHER)
    capsys.readouterr()
    return home, keychain


# ── Happy path ───────────────────────────────────────────────────────────────


def test_a_retired_token_is_replaced_and_nothing_else_changes(enrolled, monkeypatch, capsys):
    home, keychain = enrolled
    # A row enrolled before 8q, and a category mapping added by hand since.
    row = _row(home)
    del row["user_id"]
    row["categories"] = {"Platform": "alpha"}
    _write_row(home, row)
    other_row = _row_file(home, OTHER).read_bytes()
    other_sealed = _sealed(home, keychain)[OTHER]
    provider = _provider()

    code, terminal = _sign_in(monkeypatch, provider)

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert terminal.prompts == [], "the command asks nothing but the sign-in"
    assert FLOW_MESSAGE in captured.out, "Microsoft's own address and code"
    assert f"{INSTANCE}: sign-in replaced; healthy" in captured.out
    assert "No restart is needed" in captured.out
    assert provider.authorities == ["https://login.microsoftonline.com/organizations"], (
        "the row's own tenant"
    )
    assert provider.refreshed_with == [NEW_SIGNED_IN]
    assert _token_of(home, keychain) == NEW_ROTATED
    assert _row(home) == {**row, "user_id": ACCOUNT_CLAIMS["oid"]}, (
        "the row records the signed-in user, and every other key is as it was"
    )
    assert _row_file(home, OTHER).read_bytes() == other_row
    assert _sealed(home, keychain)[OTHER] == other_sealed
    _no_token_printed(captured)


def test_afterwards_connector_check_is_healthy_and_a_start_refreshes_from_it(
    enrolled, monkeypatch, capsys
):
    """The acceptance criterion: healthy, and a fresh composition uses the new token."""
    home, keychain = enrolled
    assert _sign_in(monkeypatch, _provider())[0] == EXIT_OK
    capsys.readouterr()

    next_start = Microsoft(live=NEW_ROTATED, issue=NEW_ROTATED)
    monkeypatch.setattr(wiring, "GraphDeviceCodeAuth", _faked_auth(next_start))
    entry.main(["connector", "check"])

    out = capsys.readouterr().out
    (line,) = [line for line in out.splitlines() if f"] {INSTANCE}:" in line]
    assert f"[{Health.OK.value:>7}]" in line, out
    assert NEW_ROTATED in next_start.refreshed_with

    composed = entry._compose(keychain, APPLICATION)
    connector = composed.daemon.connectors.get(INSTANCE)
    assert isinstance(connector, GraphConnector)
    fresh = Microsoft(live=NEW_ROTATED, issue=NEW_ROTATED)
    connector.auth.client_factory = fresh
    assert connector.check_health().health is Health.OK
    assert fresh.refreshed_with == [NEW_ROTATED]


def test_with_no_project_enrolled_it_runs_as_on_any_machine(enrolled, monkeypatch, capsys):
    home, keychain = enrolled
    monkeypatch.setattr(
        entry,
        "_bootstrap",
        lambda chain: Bootstrap({}, ArtifactState.absent(), bootstrap(chain).config),
    )

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "sign-in replaced; healthy" in captured.out
    assert _token_of(home, keychain) == NEW_ROTATED


# ── Refused before anything ──────────────────────────────────────────────────


def test_no_row_of_that_name_is_refused_by_name(enrolled, monkeypatch, capsys):
    home, _ = enrolled
    before = _state(home)

    code, _ = _sign_in(monkeypatch, _provider(), instance="graph:nope")

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "'graph:nope'" in captured.err and "no row" in captured.err
    assert "Nothing was asked and nothing was saved." in captured.err, "its siblings' wording"
    assert FLOW_MESSAGE not in captured.out
    assert _state(home) == before
    _no_token_printed(captured)


def test_without_a_terminal_nothing_is_shown(enrolled, monkeypatch, capsys):
    home, _ = enrolled
    before = _state(home)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "terminal" in captured.err
    assert FLOW_MESSAGE not in captured.out
    assert _state(home) == before


def test_no_master_key_names_the_command_that_enrols_one(enrolled, monkeypatch, capsys):
    home, keychain = enrolled
    before = _state(home)
    keychain.secret = None

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "pm-ai key enrol" in captured.err
    assert FLOW_MESSAGE not in captured.out
    assert _state(home) == before


def test_missing_runtime_packages_name_the_extra(enrolled, monkeypatch, capsys):
    home, _ = enrolled
    before = _state(home)
    monkeypatch.setitem(sys.modules, "msal", None)

    code, _ = _sign_in(monkeypatch, None)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "uv sync --extra runtime" in captured.err
    assert FLOW_MESSAGE not in captured.out
    assert _state(home) == before


def test_no_graph_sign_in_supplied_is_a_wiring_fault_not_a_traceback(capsys):
    code = cli.dispatch(
        ["connector", "sign-in", INSTANCE],
        daemon=None,
        diagnose=lambda: None,
        probe_connectors=None,
    )
    assert code == EXIT_REFUSAL
    assert "wiring fault" in capsys.readouterr().err


# ── Wrong or missing system ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("system", "said"),
    [
        ("gitlab", "'gitlab' connector, not a Graph one"),
        (None, "names no `system`"),
        (7, "`system` that is a int, not text"),
    ],
    ids=["gitlab", "no system", "not text"],
)
def test_a_row_that_is_not_graph_s_is_refused_naming_the_system(
    enrolled, monkeypatch, capsys, system, said
):
    home, _ = enrolled
    row = _row(home)
    if system is None:
        del row["system"]
    else:
        row["system"] = system
    _write_row(home, row)
    before = _state(home)

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert said in captured.err
    assert FLOW_MESSAGE not in captured.out
    assert _state(home) == before


def test_a_gitlab_connector_is_refused(machine, monkeypatch, capsys):
    """`gitlab:alpha`, enrolled as GitLab: there is no Microsoft sign-in to redo."""
    home, _ = machine
    rows = home / ".pm-ai" / "connectors"
    rows.mkdir(parents=True)
    (rows / "gitlab:alpha.json").write_text(
        json.dumps({"instance": "gitlab:alpha", "system": "gitlab", "enabled": True})
    )

    code, _ = _sign_in(monkeypatch, _provider(), instance="gitlab:alpha")

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "'gitlab' connector" in captured.err
    assert FLOW_MESSAGE not in captured.out


def test_a_sealed_entry_recorded_under_another_system_is_refused(
    enrolled, monkeypatch, capsys
):
    home, keychain = enrolled
    _rewrite_sealed(
        home, keychain, lambda d: d["connectors"][INSTANCE].update(system="gitlab")
    )
    before = _state(home)

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "'gitlab'" in captured.err and "'graph'" in captured.err, "both systems named"
    assert FLOW_MESSAGE not in captured.out
    assert _state(home) == before


# ── Disabled row ─────────────────────────────────────────────────────────────


def test_a_row_switched_off_is_signed_in_and_stays_off(enrolled, monkeypatch, capsys):
    home, keychain = enrolled
    row = _row(home)
    row["enabled"] = False
    _write_row(home, row)

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "sign-in replaced; healthy" in captured.out
    assert _row(home)["enabled"] is False
    assert _token_of(home, keychain) == NEW_ROTATED


# ── A row that would not build ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("change", "key"),
    [
        (lambda row: row.pop("client_id"), "client_id"),
        (lambda row: row.update(client_id="  "), "client_id"),
        (lambda row: row.update(tenant="contoso/evil"), "tenant"),
        (lambda row: row.pop("window_width_minutes"), "window_width_minutes"),
        (lambda row: row.update(window_width_minutes=120), "240"),
        (lambda row: row.update(first_run_reach_back_minutes="a week"), "first_run_reach_back_minutes"),
    ],
    ids=["no client id", "blank client id", "malformed tenant", "no width", "width under the floor", "reach-back not minutes"],
)
def test_a_row_that_would_not_build_is_refused_naming_the_key(
    enrolled, monkeypatch, capsys, change, key
):
    home, _ = enrolled
    row = _row(home)
    change(row)
    _write_row(home, row)
    before = _state(home)
    provider = _provider()

    code, terminal = _sign_in(monkeypatch, provider)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "would not build" in captured.err and key in captured.err
    assert terminal.prompts == [] and FLOW_MESSAGE not in captured.out
    assert provider.authorities == [], "Microsoft was asked"
    assert _state(home) == before


# ── The sign-in and the check refuse ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("device", "reason"),
    [
        ({"error": "access_denied", "error_description": "the user declined"}, "declined"),
        ({"error": "expired_token", "error_description": "code expired"}, "expired"),
        ({**_token(NEW_SIGNED_IN), "scope": "https://graph.microsoft.com/Calendars.Read"}, "Chat.Read"),
    ],
    ids=["declined", "code expired", "permission not granted"],
)
def test_a_sign_in_that_fails_leaves_the_stored_one_untouched(
    enrolled, monkeypatch, capsys, device, reason
):
    home, _ = enrolled
    before = _state(home)

    code, _ = _sign_in(monkeypatch, Microsoft(device=device, issue=NEW_ROTATED))

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert reason in captured.err
    assert _state(home) == before
    _no_token_printed(captured)


def test_a_failing_health_check_leaves_it_untouched(enrolled, monkeypatch, capsys):
    home, _ = enrolled
    before = _state(home)
    rejected = {"error": "invalid_grant", "error_description": "AADSTS70000: revoked"}

    code, _ = _sign_in(monkeypatch, Microsoft(device=_token(NEW_SIGNED_IN), refresh=rejected))

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "health check failed" in captured.err
    assert _state(home) == before
    _no_token_printed(captured)


def test_a_health_check_past_its_bound_leaves_it_untouched(enrolled, monkeypatch, capsys):
    import threading

    home, _ = enrolled
    before = _state(home)
    hold = threading.Event()
    provider = Microsoft(device=_token(NEW_SIGNED_IN), hold_refresh=hold)
    try:
        code, _ = _sign_in(monkeypatch, provider, timeout=0.2)
    finally:
        hold.set()

    assert code == EXIT_REFUSAL
    assert "did not answer within 0.2s" in capsys.readouterr().err
    assert _state(home) == before


def test_a_health_check_that_warns_saves_and_says_so(enrolled, monkeypatch, capsys):
    home, keychain = enrolled

    class Skewed(Microsoft):
        def acquire_token_by_refresh_token(self, refresh_token, scopes):
            answer = super().acquire_token_by_refresh_token(refresh_token, scopes)
            answer["id_token_claims"] = {**ACCOUNT_CLAIMS, "iat": 0}
            return answer

    code, _ = _sign_in(monkeypatch, Skewed(device=_token(NEW_SIGNED_IN), issue=NEW_ROTATED))

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "Fix the clock" in captured.out, "the warning is printed"
    assert f"{INSTANCE}: sign-in replaced; warned" in captured.out
    assert f"{INSTANCE}: sign-in replaced; healthy" not in captured.out
    assert _token_of(home, keychain) == NEW_ROTATED


# ── Credentials file busy ────────────────────────────────────────────────────


def test_a_credentials_file_busy_past_the_wait_is_refused_as_busy(
    enrolled, monkeypatch, capsys
):
    home, keychain = enrolled
    before = _state(home)
    storage = build(home, "alpha", keychain=keychain).storage

    with held_by_another_thread(
        lambda: storage.exclusive(scope=APPLICATION, artifact="config.json")
    ):
        code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "busy" in captured.err and "config.json" in captured.err
    assert "sign-in replaced" not in captured.out
    assert _state(home) == before
    _no_token_printed(captured)


# ── Sealed entry or file absent ──────────────────────────────────────────────


def test_a_missing_entry_is_written_beside_the_others(enrolled, monkeypatch, capsys):
    """The "rotated token had nowhere to go" case, which now has a remedy."""
    home, keychain = enrolled
    _rewrite_sealed(home, keychain, lambda d: d["connectors"].pop(INSTANCE))
    other = _sealed(home, keychain)[OTHER]

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "sign-in replaced" in captured.out
    assert _token_of(home, keychain) == NEW_ROTATED
    assert _sealed(home, keychain)[OTHER] == other


def test_no_credentials_file_at_all_is_created(enrolled, monkeypatch, capsys):
    home, keychain = enrolled
    _sealed_file(home).unlink()

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "sign-in replaced" in captured.out
    assert set(_sealed(home, keychain)) == {INSTANCE}
    assert _token_of(home, keychain) == NEW_ROTATED


# ── Any authenticated account is accepted (Andrei, 2026-10-12) ──────────────


def test_a_different_user_is_accepted_and_recorded(enrolled, monkeypatch, capsys):
    home, keychain = enrolled
    row = _row(home)

    code, _ = _sign_in(monkeypatch, _provider(oid="someone-else"))

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert "sign-in replaced; healthy" in captured.out
    assert _row(home) == {**row, "user_id": "someone-else"}
    assert _token_of(home, keychain) == NEW_ROTATED
    _no_token_printed(captured)


def test_a_different_tenant_is_accepted_and_its_user_recorded(machine, monkeypatch, capsys):
    home, keychain = machine
    _add(monkeypatch, answers=[CLIENT_ID, ROW_TENANT, "1440", "10080"])
    capsys.readouterr()

    code, _ = _sign_in(monkeypatch, _provider(oid="a-guest", tid=OTHER_TENANT))

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert _row(home)["user_id"] == "a-guest"
    assert _row(home)["tenant"] == ROW_TENANT, "the row's settings are untouched"
    assert _token_of(home, keychain) == NEW_ROTATED


def test_the_row_s_own_tenant_id_is_signed_in_through(machine, monkeypatch, capsys):
    home, keychain = machine
    _add(monkeypatch, answers=[CLIENT_ID, ROW_TENANT, "1440", "10080"])
    capsys.readouterr()
    provider = _provider(tid=ROW_TENANT.upper())

    code, _ = _sign_in(monkeypatch, provider)

    assert code == EXIT_OK, capsys.readouterr().err
    assert provider.authorities == [f"https://login.microsoftonline.com/{ROW_TENANT}"]
    assert _token_of(home, keychain) == NEW_ROTATED


# ── Row write fails after the credential landed ──────────────────────────────


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes through a 0o500 directory, so the write cannot be made to fail",
)
def test_a_row_write_that_fails_after_the_credential_landed_says_exactly_that(
    enrolled, monkeypatch, capsys
):
    home, keychain = enrolled
    row = _row(home)
    del row["user_id"]
    _write_row(home, row)
    rows = _row_file(home).parent

    class LocksTheRowsDuringTheSignIn(Microsoft):
        def acquire_token_by_device_flow(self, flow):
            rows.chmod(0o500)
            return super().acquire_token_by_device_flow(flow)

    try:
        code, _ = _sign_in(
            monkeypatch, LocksTheRowsDuringTheSignIn(device=_token(NEW_SIGNED_IN), issue=NEW_ROTATED)
        )
    finally:
        rows.chmod(0o700)

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "sign-in replaced; the user id was not recorded" in captured.err
    assert f"Run {COMMAND} again to record the user id" in captured.err
    assert f"{INSTANCE} obtained a token" in captured.out, (
        "the sign-in landed, so the check's answer is printed"
    )
    assert "sign-in replaced; healthy" not in captured.out, "no closing success line"
    assert _token_of(home, keychain) == NEW_ROTATED, "the credential did land"
    assert "user_id" not in _row(home)
    _no_token_printed(captured)


# ── Remedies name the command ────────────────────────────────────────────────


def test_connector_check_on_a_retired_credential_names_the_command(
    enrolled, monkeypatch, capsys
):
    """What the PM sees first: the check's remedy is the command to run."""
    retired = Microsoft(live="a-token-nobody-holds")
    monkeypatch.setattr(wiring, "GraphDeviceCodeAuth", _faked_auth(retired))

    entry.main(["connector", "check"])

    out = capsys.readouterr().out
    assert COMMAND in out, out
    for retired_wording in ("Sign in again", "enrol again", "has no command"):
        assert retired_wording not in out


# ── Review round: concurrency ────────────────────────────────────────────────


class _Lineages:
    """Microsoft where every token that was ever issued stays live, as in fact.

    A sign-in starts a new lineage and does not retire the old one, so a
    refresh racing it succeeds — which is what makes an unconditional
    write-back able to overwrite the new sign-in. `during` runs once, inside
    the first redemption. `cached` is MSAL's per-process cache: the refresh
    token `acquire_token_silent` would use for the cached account.
    """

    def __init__(self, *, during=None, accounts=()):
        self.during = during
        self.accounts = [dict(a) for a in accounts]
        self.cached: str | None = None
        self.refreshed_with: list[str] = []
        self.silent_with: list[str] = []

    def __call__(self, client_id, authority):
        return self

    def _issue(self, refresh_token: str) -> dict:
        successor = f"successor-of-{refresh_token}"
        self.cached = successor
        return _token(successor)

    def acquire_token_by_refresh_token(self, refresh_token, scopes):
        self.refreshed_with.append(refresh_token)
        if self.during is not None:
            hook, self.during = self.during, None
            hook()
        return self._issue(refresh_token)

    def acquire_token_silent(self, scopes, account, force_refresh=False):
        if self.cached is None:
            return None
        self.silent_with.append(self.cached)
        return self._issue(self.cached)

    def get_accounts(self, username=None):
        return [dict(a) for a in self.accounts]


def _connector_auth(home: Path, keychain, provider) -> object:
    """The connector a running daemon would hold, talking to `provider`."""
    composed = entry._compose(keychain, APPLICATION)
    connector = composed.daemon.connectors[INSTANCE]
    connector.auth.client_factory = provider
    return connector.auth


def test_a_refresh_racing_a_sign_in_keeps_the_new_sign_in_and_uses_it(
    enrolled, monkeypatch, capsys
):
    """Compare-and-set: the refresh read the old token, the sign-in landed, and
    the old lineage's successor is not written over it."""
    home, keychain = enrolled
    signed_in: list[int] = []

    def a_sign_in_lands() -> None:
        signed_in.append(_sign_in(monkeypatch, _provider())[0])

    racing = _Lineages(during=a_sign_in_lands)
    auth = _connector_auth(home, keychain, racing)

    auth.access_token(force_refresh=True)

    assert signed_in == [EXIT_OK], capsys.readouterr().err
    assert racing.refreshed_with == [ROTATED, NEW_ROTATED], (
        "the stored sign-in was not the one used after the race"
    )
    assert _token_of(home, keychain) == f"successor-of-{NEW_ROTATED}"
    assert f"successor-of-{ROTATED}" not in json.dumps(_sealed(home, keychain))


def test_an_auth_built_before_the_sign_in_refreshes_from_the_new_one(
    enrolled, monkeypatch, capsys
):
    """What the closing line promises: no restart, and the old lineage in MSAL's
    cache is not what the next refresh uses."""
    home, keychain = enrolled
    provider = _Lineages(accounts=[{"home_account_id": "an-object-id.a-tenant-id"}])
    auth = _connector_auth(home, keychain, provider)
    auth.access_token(force_refresh=True)
    assert provider.cached == f"successor-of-{ROTATED}"

    assert _sign_in(monkeypatch, _provider())[0] == EXIT_OK, capsys.readouterr().err
    provider.silent_with.clear()
    provider.refreshed_with.clear()

    auth.access_token(force_refresh=True)

    assert provider.silent_with == [], "the cache's old lineage was refreshed"
    assert provider.refreshed_with == [NEW_ROTATED]
    assert _token_of(home, keychain) == f"successor-of-{NEW_ROTATED}"


class _DuringTheDeviceFlow(Microsoft):
    """The new sign-in's Microsoft, running `during` while the person is in the browser."""

    def __init__(self, during, **kwargs):
        super().__init__(device=_token(NEW_SIGNED_IN), issue=NEW_ROTATED, **kwargs)
        self.during = during

    def acquire_token_by_device_flow(self, flow):
        self.during()
        return super().acquire_token_by_device_flow(flow)


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda row: row.update(client_id="another-app"), "client_id"),
        (lambda row: row.update(tenant=ROW_TENANT), "tenant"),
        (lambda row: row.update(window_width_minutes=480), "window_width_minutes"),
        (lambda row: row.update(system="gitlab"), "'gitlab' connector"),
    ],
    ids=["client id", "tenant", "width", "system"],
)
def test_a_row_changed_during_the_sign_in_is_refused_inside_the_claim(
    enrolled, monkeypatch, capsys, change, said
):
    home, _ = enrolled
    sealed_before = _sealed_file(home).read_bytes()
    edited: dict = {}

    def edit() -> None:
        row = _row(home)
        change(row)
        _write_row(home, row)
        edited.update(row)

    code, _ = _sign_in(monkeypatch, _DuringTheDeviceFlow(edit))

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.out
    assert "changed during the sign-in" in captured.err and said in captured.err
    assert _sealed_file(home).read_bytes() == sealed_before
    assert _row(home) == edited, "the row is as the edit left it"
    _no_token_printed(captured)


def test_a_user_recorded_during_the_sign_in_is_replaced_by_the_one_who_signed_in(
    enrolled, monkeypatch, capsys
):
    """Nothing compares users: whoever signed in is recorded, over anything."""
    home, keychain = enrolled
    row = _row(home)
    del row["user_id"]
    _write_row(home, row)

    def another_user_recorded() -> None:
        _write_row(home, {**row, "user_id": "someone-else"})

    code, _ = _sign_in(monkeypatch, _DuringTheDeviceFlow(another_user_recorded))

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row(home) == {**row, "user_id": ACCOUNT_CLAIMS["oid"]}
    assert _token_of(home, keychain) == NEW_ROTATED


def test_a_category_mapping_added_during_the_sign_in_survives(
    enrolled, monkeypatch, capsys
):
    home, keychain = enrolled
    row = _row(home)
    del row["user_id"]
    _write_row(home, row)

    def mapped() -> None:
        _write_row(home, {**row, "categories": {"Platform": "alpha"}})

    code, _ = _sign_in(monkeypatch, _DuringTheDeviceFlow(mapped))

    assert code == EXIT_OK, capsys.readouterr().err
    assert _row(home) == {**row, "categories": {"Platform": "alpha"}, "user_id": "an-object-id"}
    assert _token_of(home, keychain) == NEW_ROTATED


def test_a_row_deleted_during_the_sign_in_is_refused(enrolled, monkeypatch, capsys):
    home, _ = enrolled
    sealed_before = _sealed_file(home).read_bytes()

    code, _ = _sign_in(monkeypatch, _DuringTheDeviceFlow(lambda: _row_file(home).unlink()))

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "changed during the sign-in" in captured.err and "no row" in captured.err
    assert _sealed_file(home).read_bytes() == sealed_before
    assert not _row_file(home).exists()


def test_a_sealed_entry_switched_to_another_system_during_the_sign_in_is_refused(
    enrolled, monkeypatch, capsys
):
    home, keychain = enrolled
    row_before = _row_file(home).read_bytes()
    switched: list[bytes] = []

    def switch() -> None:
        _rewrite_sealed(
            home, keychain, lambda d: d["connectors"][INSTANCE].update(system="gitlab")
        )
        switched.append(_sealed_file(home).read_bytes())

    code, _ = _sign_in(monkeypatch, _DuringTheDeviceFlow(switch))

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "'gitlab'" in captured.err
    assert [_sealed_file(home).read_bytes()] == switched
    assert _row_file(home).read_bytes() == row_before


# ── Review round: refusals that were tracebacks or misworded ─────────────────


def test_a_blank_signed_in_user_id_is_a_named_refusal(enrolled, monkeypatch, capsys):
    home, _ = enrolled
    before = _state(home)
    monkeypatch.setattr(wiring.SealedCredential, "user_id", property(lambda self: "  "))

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL, captured.err
    assert "Graph user id" in captured.err and "Traceback" not in captured.err
    assert _state(home) == before


@pytest.mark.parametrize(
    "document", [[1, 2], {"connectors": ["not", "an", "object"]}], ids=["list", "connectors list"]
)
@pytest.mark.parametrize("command", ["sign-in", "add"])
def test_a_malformed_sealed_store_is_refused_naming_it(
    enrolled, monkeypatch, capsys, document, command
):
    home, keychain = enrolled
    storage = build(home, "alpha", keychain=keychain).storage
    storage.write_artifact(json.dumps(document).encode(), scope=APPLICATION, artifact="config.json")
    before = _state(home)

    if command == "sign-in":
        code, _ = _sign_in(monkeypatch, _provider())
    else:
        _microsoft(monkeypatch, _provider())
        monkeypatch.setattr("builtins.input", Terminal(list(VALID)))
        code = entry.main(["connector", "add", "graph", "graph:third"])

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "config.json" in captured.err and "Traceback" not in captured.err
    assert FLOW_MESSAGE not in captured.out
    assert _state(home) == before


def test_a_claim_that_cannot_be_taken_is_its_own_refusal(enrolled, monkeypatch, capsys):
    """The lock path is a directory: not busy, and not a failed check."""
    home, _ = enrolled
    sidecar = _sealed_file(home).with_name("config.json.lock")
    if sidecar.exists():
        sidecar.unlink()
    sidecar.mkdir()
    before = _state(home)

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "config.json" in captured.err and "could not be taken" in captured.err
    assert "busy" not in captured.err and "health check" not in captured.err
    assert _state(home) == before


def test_a_message_from_a_failed_row_write_never_quotes_the_token(
    enrolled, monkeypatch, capsys
):
    home, _ = enrolled

    def quoting(*args, **kwargs):
        raise OSError(f"could not write, holding {NEW_ROTATED}")

    monkeypatch.setattr(wiring, "record_user_id", quoting)

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "user id was not recorded" in captured.err and "withheld" in captured.err
    assert ".." not in captured.err and ". ." not in captured.err
    _no_token_printed(captured)


def test_with_encryption_off_and_no_daemon_the_warning_is_printed(
    machine, monkeypatch, capsys
):
    home, keychain = machine
    monkeypatch.setenv("PM_AI_DISABLE_ENCRYPTION", "1")
    _add(monkeypatch)
    monkeypatch.setattr(
        entry,
        "_bootstrap",
        lambda chain: Bootstrap({}, ArtifactState.absent(), bootstrap(chain).config),
    )
    capsys.readouterr()

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    assert captured.err.count("encryption is disabled") == 1


def test_an_instance_name_that_cannot_be_a_row_is_refused(enrolled, monkeypatch, capsys):
    code, _ = _sign_in(monkeypatch, _provider(), instance="../graph")
    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "'../graph'" in captured.err and "Traceback" not in captured.err


def test_an_unreachable_keychain_is_refused(enrolled, monkeypatch, capsys):
    from pm_ai.ports import KeychainUnavailable

    home, keychain = enrolled
    before = _state(home)

    def unreachable(name):
        raise KeychainUnavailable("the keychain could not be asked")

    keychain.fetch = unreachable

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert "the keychain could not be asked" in captured.err
    assert FLOW_MESSAGE not in captured.out
    assert _state(home) == before


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"], ids=["not json", "not an object"])
def test_a_malformed_row_is_refused_naming_it(enrolled, monkeypatch, capsys, text):
    home, _ = enrolled
    _row_file(home).write_text(text)
    before = _state(home)

    code, _ = _sign_in(monkeypatch, _provider())

    captured = capsys.readouterr()
    assert code == EXIT_REFUSAL
    assert f"{INSTANCE}.json" in captured.err
    assert "Nothing was asked and nothing was saved." in captured.err
    assert _state(home) == before
