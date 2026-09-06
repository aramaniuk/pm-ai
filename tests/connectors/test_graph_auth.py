"""Story 33a — the Graph device-code flow, one test per matrix row.

Every test runs against a fake MSAL client. **No test opens a socket**, and the
fake is what makes that structural rather than a promise: the adapter reaches
MSAL through an injected `client_factory`, so a test that forgot to supply one
would try to build a real `msal.PublicClientApplication` — and `msal` is not
installed in this environment, which turns the omission into a failure rather
than a network call.

The fake answers with the dicts MSAL really returns. Their shapes were checked
against msal 1.38.0 rather than remembered: `initiate_device_flow` returns
`user_code`/`verification_uri`/`interval`/`expires_in`/`message` or an `error`,
`acquire_token_by_device_flow` returns a token response or an `error`, and
`acquire_token_silent` returns `None` when the cache has nothing to offer.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone

import pytest

from pm_ai.connectors.graph.auth import (
    CLOCK_SKEW_TOLERANCE,
    EXPIRY_MARGIN,
    GRAPH_RESOURCE_SCOPES,
    GRAPH_SCOPES,
    OFFLINE_ACCESS,
    AuthDeclined,
    AuthTimedOut,
    CredentialStale,
    GraphAuthError,
    GraphDeviceCodeAuth,
    GraphUnreachable,
    InMemoryRefreshTokenStore,
    InteractionRequired,
    SealedCredential,
    _WITHHELD,
)
from pm_ai.domain.health import Health

NOW = datetime(2026, 9, 6, 9, 0, tzinfo=timezone.utc)
CLIENT_ID = "00000000-0000-0000-0000-000000000000"
ACCOUNT = "an-object-id.a-tenant-id"
REFRESH = "an-opaque-refresh-token-that-must-never-be-printed"
ROTATED = "the-refresh-token-aad-issued-in-its-place"
ACCESS = "an-access-token"

GRANTED = " ".join(f"https://graph.microsoft.com/{s}" for s in sorted(GRAPH_RESOURCE_SCOPES))
"""Exactly what the app registration is expected to grant, as AAD spells it back.

Full URIs deliberately: AAD echoes some responses that way and some bare, and a
comparison that only handled the bare form would refuse a real, complete
consent.
"""


def _token(**overrides) -> dict:
    """A successful token response, with the fields the adapter reads."""
    result = {
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "expires_in": 3600,
        "scope": GRANTED,
        "token_type": "Bearer",
        "id_token_claims": {"oid": "an-object-id", "tid": "a-tenant-id"},
    }
    result.update(overrides)
    return result


def _flow(**overrides) -> dict:
    """What `initiate_device_flow` returns once AAD has issued a code."""
    flow = {
        "user_code": "ABCD-EFGH",
        "device_code": "a-device-code",
        "verification_uri": "https://microsoft.com/devicelogin",
        "expires_in": 900,
        "interval": 5,
        "message": "To sign in, use a web browser to open https://microsoft.com/"
        "devicelogin and enter the code ABCD-EFGH to authenticate.",
    }
    flow.update(overrides)
    return flow


class FakeMsal:
    """A `PublicClientApplication`, as much of one as this adapter touches.

    Records what it was asked, because several matrix rows are about *how* the
    adapter asks rather than about the answer: which scopes it requests, whether
    it hands the flow object back untouched, and whether it selects an account
    or takes the first one.
    """

    def __init__(
        self,
        *,
        flow: dict | None = None,
        device_result: dict | None = None,
        refresh_results: list[dict] | None = None,
        silent_result: dict | None = None,
        accounts: list[dict] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._flow = flow if flow is not None else _flow()
        self._device_result = device_result if device_result is not None else _token()
        self._refresh_results = list(refresh_results or [_token()])
        self._silent_result = silent_result
        self._accounts = accounts if accounts is not None else []
        self._raises = raises
        self.initiated_with: list | None = None
        self.polled_with: list[dict] = []
        self.refreshed_with: list[tuple[str, list]] = []
        self.silent_calls: list[dict] = []

    def initiate_device_flow(self, scopes):
        if self._raises is not None:
            raise self._raises
        self.initiated_with = list(scopes)
        return dict(self._flow)

    def acquire_token_by_device_flow(self, flow, *args, **kwargs):
        if self._raises is not None:
            raise self._raises
        assert not args and not kwargs, (
            "the adapter passed extra arguments to the polling call. An "
            "`exit_condition` here would cut MSAL's own poll short, which is the "
            "loop that honours the interval AAD set."
        )
        self.polled_with.append(flow)
        return dict(self._device_result)

    def acquire_token_by_refresh_token(self, refresh_token, scopes):
        if self._raises is not None:
            raise self._raises
        self.refreshed_with.append((refresh_token, list(scopes)))
        # The last answer repeats. A provider that spontaneously started
        # accepting once a scripted list ran out would let a test assert a
        # refusal and then observe success from the very same state — which is
        # how `check_health` was measured as OK against a credential the
        # acquisition above had just reported stale.
        answer = self._refresh_results.pop(0) if len(self._refresh_results) > 1 else self._refresh_results[0]
        return dict(answer)

    def acquire_token_silent(self, scopes, account, force_refresh=False):
        if self._raises is not None:
            raise self._raises
        self.silent_calls.append({"account": account, "force_refresh": force_refresh})
        return dict(self._silent_result) if self._silent_result else None

    def get_accounts(self, username=None):
        return [dict(a) for a in self._accounts]


def _auth(client: FakeMsal, *, credential: str | None = None, now=None, **kwargs):
    """The adapter under test, wired to `client` and to a token store in memory."""
    return GraphDeviceCodeAuth(
        client_id=CLIENT_ID,
        store=InMemoryRefreshTokenStore(credential),
        client_factory=lambda client_id, authority: client,
        now=now or (lambda: NOW),
        **kwargs,
    )


def _enrolled(refresh_token: str = REFRESH) -> str:
    return SealedCredential(refresh_token=refresh_token, home_account_id=ACCOUNT).encode()


# ── The scope set ────────────────────────────────────────────────────────────


def test_the_declared_scope_set_is_exactly_seven_permissions_plus_offline_access():
    """Asserted as a set, so an addition *or* an omission fails.

    The omission direction is the one a live tenant exercised: the set was short
    by `OnlineMeetings.Read` and nothing failed until Graph answered 403 in a way
    that read as a tenant restriction. The addition direction is the ordinary
    drift — somebody adds `Mail.Read` for a feature and the consent prompt
    silently widens over something the PM already agreed to.
    """
    assert GRAPH_RESOURCE_SCOPES == {
        "Calendars.Read",
        "Chat.Read",
        "ChannelMessage.Read.All",
        "OnlineMeetingTranscript.Read.All",
        "OnlineMeetings.Read",
        "Team.ReadBasic.All",
        "Channel.ReadBasic.All",
    }
    assert len(GRAPH_RESOURCE_SCOPES) == 7
    assert GRAPH_SCOPES == GRAPH_RESOURCE_SCOPES | {OFFLINE_ACCESS}
    assert OFFLINE_ACCESS not in GRAPH_RESOURCE_SCOPES, (
        "offline_access is not a resource permission; counting it as one is how "
        "'seven scopes' quietly becomes six"
    )


def test_offline_access_is_declared_and_never_requested():
    """The declared set and the requested list differ, and the difference is measured.

    msal 1.38.0 refuses `offline_access` as a user-provided scope — "You cannot
    use any scope value that is reserved. The reserved list: ['offline_access',
    'openid', 'profile']" — and adds it itself. So an adapter that passed the
    declared set straight through would raise `ValueError` on every sign-in.
    """
    client = FakeMsal()
    auth = _auth(client)
    auth.sign_in(lambda message: None)

    assert client.initiated_with == sorted(GRAPH_RESOURCE_SCOPES)
    assert OFFLINE_ACCESS not in (client.initiated_with or [])


# ── First sign-in ────────────────────────────────────────────────────────────


def test_a_first_sign_in_presents_a_code_and_seals_the_refresh_token():
    """The matrix's first row: no stored token, a code and a URL, then a seal."""
    client = FakeMsal()
    auth = _auth(client)
    shown: list[str] = []

    credential = auth.sign_in(shown.append)

    assert len(shown) == 1
    assert "ABCD-EFGH" in shown[0] and "devicelogin" in shown[0]
    sealed = SealedCredential.decode(credential)
    assert sealed.refresh_token == REFRESH
    assert sealed.home_account_id == ACCOUNT
    assert auth.store.read() == credential


def test_signing_in_opens_no_browser_and_asks_for_no_password():
    """The human does the signing in. The adapter's whole interaction is a string.

    Asserted over the module's **syntax tree**, not its text. A substring scan
    read comments and docstrings as violations — the word "browser" in a sentence
    explaining that no browser is opened failed it — while missing every spelling
    that does not contain the searched word: `os.system("open ...")`,
    `subprocess.run([...])`, `sys.stdin.readline()`. Imports and call targets are
    what actually launch something, so those are what this reads.
    """
    import ast
    import pathlib

    import pm_ai.connectors.graph.auth as module

    source = module.__file__
    assert source is not None
    tree = ast.parse(pathlib.Path(source).read_text(encoding="utf-8"))

    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                called.add(target.id)
            elif isinstance(target, ast.Attribute):
                called.add(target.attr)

    forbidden_imports = {"webbrowser", "getpass", "subprocess", "os", "pty", "tty"}
    assert not (forbidden_imports & imported), (
        f"{sorted(forbidden_imports & imported)} imported by the device-code "
        f"adapter — the PM signs in themselves, wherever they already are"
    )
    forbidden_calls = {
        "input",
        "getpass",
        "system",
        "popen",
        "Popen",
        "startfile",
        "open_new",
        "open_new_tab",
        "readline",
    }
    assert not (forbidden_calls & called), (
        f"{sorted(forbidden_calls & called)} called by the device-code adapter — "
        f"it prints a code and waits; it never reads a password or launches a UI"
    )


def test_the_polling_interval_the_provider_set_is_the_one_that_is_honoured():
    """Throttling is MSAL's loop to handle, and this is what keeps it that way.

    msal 1.38.0 polls until the flow expires, honours `interval`, and adds RFC
    8628's five seconds when the endpoint answers `slow_down`. The adapter's job
    is to not get in the way: hand the flow object back exactly as it arrived and
    supply no `exit_condition`, which would end the poll early and report an
    abandoned sign-in that was only being throttled.
    """
    client = FakeMsal()
    auth = _auth(client)
    auth.sign_in(lambda message: None)

    (polled,) = client.polled_with
    assert polled["interval"] == 5, "the interval AAD set was altered"
    assert polled["expires_in"] == 900
    assert polled["device_code"] == "a-device-code"


def test_an_abandoned_sign_in_is_refused_and_stores_nothing():
    """The PM walked away; the code expired unused.

    msal's polling loop returns its last *retriable* error once the flow's own
    expiry passes, so a terminal `authorization_pending` is precisely "nobody
    used this code" rather than "we were being throttled".
    """
    client = FakeMsal(device_result={"error": "authorization_pending"})
    auth = _auth(client)

    with pytest.raises(AuthTimedOut) as refused:
        auth.sign_in(lambda message: None)

    assert "expired" in str(refused.value)
    assert auth.store.read() is None, "an abandoned sign-in left a credential behind"


def test_throttling_is_a_wait_and_not_an_abandoned_sign_in():
    """The matrix's throttling row: honoured and backed off, "retried, not counted
    as abandoned".

    `slow_down` sat in `_ABANDONED`, and every member of that set renders as "the
    device code expired before anyone signed in with it" — so a PM who was
    watching the code being rate-limited was told they had walked away from it,
    and `AuthTimedOut` says to start over rather than to wait.
    """
    for throttled in (
        {"error": "slow_down", "error_description": "AADSTS90101: too many requests"},
        {"error": "temporarily_unavailable"},
        {"error": "an_unmapped_code", "http_status": 429},
    ):
        auth = _auth(FakeMsal(device_result=throttled))
        with pytest.raises(GraphUnreachable) as refused:
            auth.sign_in(lambda message: None)
        assert not isinstance(refused.value, AuthTimedOut), f"{throttled} read as abandoned"
        assert "throttled" in str(refused.value)
        assert auth.store.read() is None


def test_a_bad_app_registration_is_not_reported_as_a_stale_credential():
    """`unauthorized_client` is the registration, and no sign-in fixes one.

    Mapped to `CredentialStale` it produced "Sign in again; this is the
    credential, not the network" — a remedy that cannot work, for a fault that is
    neither the credential nor the network.
    """
    client = FakeMsal(
        refresh_results=[
            {
                "error": "unauthorized_client",
                "error_description": "AADSTS7000218: the request body must contain "
                "client_assertion or client_secret",
            }
        ]
    )
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(GraphAuthError) as refused:
        auth.access_token()

    assert not isinstance(refused.value, CredentialStale)
    assert "app registration" in str(refused.value)
    assert "Signing in again cannot fix it" in str(refused.value)
    assert auth.check_health().health is Health.FAILING


def test_a_declined_consent_names_what_was_declined():
    client = FakeMsal(
        device_result={
            "error": "access_denied",
            "error_description": "the user declined Calendars.Read",
        }
    )
    auth = _auth(client)

    with pytest.raises(AuthDeclined) as refused:
        auth.sign_in(lambda message: None)

    assert "Calendars.Read" in str(refused.value)
    assert auth.store.read() is None


def test_a_sign_in_without_a_refresh_token_names_offline_access():
    """The one scope that cannot be checked by string comparison.

    AAD does not echo `offline_access` in the granted list and MSAL will not let
    it be requested by name, so the measurement is whether a refresh token came
    back. Without one the connector would prompt for a sign-in every hour, which
    is not a connector.
    """
    client = FakeMsal(device_result=_token(refresh_token=None))
    auth = _auth(client)

    with pytest.raises(AuthDeclined) as refused:
        auth.sign_in(lambda message: None)

    assert OFFLINE_ACCESS in str(refused.value)
    assert auth.store.read() is None


def test_a_sign_in_with_no_access_token_seals_nothing():
    """The last refusal in `sign_in` used to land *after* the write.

    `_adopt` refuses a response carrying no usable access token, and it ran after
    `store.write` — so this path sealed a credential that `sign_in` then never
    returned: an enrolment nobody completed, holding a token nothing on the
    machine refers to. Every other refusal here is asserted with an empty store,
    and this one now can be too.
    """
    client = FakeMsal(device_result=_token(access_token=None))
    auth = _auth(client)

    with pytest.raises(GraphAuthError) as refused:
        auth.sign_in(lambda message: None)

    assert "without an access token" in str(refused.value)
    assert auth.store.read() is None, "a refused sign-in left a credential sealed"


def test_a_refresh_token_that_is_not_a_string_is_refused_before_it_is_sealed():
    """Truthy is not usable, and the next start is the wrong place to find out.

    Sealed unchecked, a list or a number became a `CredentialStale` at the next
    decode — one process away from the sign-in that caused it, and reported as an
    expired credential rather than as a response pm-ai cannot use.
    """
    client = FakeMsal(device_result=_token(refresh_token=["not", "a", "string"]))
    auth = _auth(client)

    with pytest.raises(GraphAuthError) as refused:
        auth.sign_in(lambda message: None)

    assert "not a string" in str(refused.value)
    assert auth.store.read() is None


def test_an_unidentifiable_enrolling_account_is_stale_and_not_declined():
    """The matrix's ambiguous-cache row names `CredentialStale`, in both directions.

    `_cached_account` raised `CredentialStale` for exactly this fact while
    `_enrolling_account` raised `AuthDeclined` — which says consent was refused,
    when consent had just succeeded and only the identity was missing.
    `AuthDeclined`'s own docstring scopes it to consent.
    """
    client = FakeMsal(
        device_result=_token(id_token_claims={}),
        accounts=[
            {"home_account_id": "one-account.a-tenant"},
            {"home_account_id": "another.a-tenant"},
        ],
    )
    auth = _auth(client)

    with pytest.raises(CredentialStale) as refused:
        auth.sign_in(lambda message: None)

    assert not isinstance(refused.value, AuthDeclined)
    assert "which account" in str(refused.value)
    assert auth.store.read() is None


# ── Silent refresh ───────────────────────────────────────────────────────────


def test_a_valid_refresh_token_yields_an_access_token_without_prompting():
    """Prompting is asserted against the client, not against a list nobody wrote to.

    The earlier version built a `shown` list, handed it to nothing, and asserted
    it was empty — plus `auth_present is not None` on a bound method, which no
    implementation could ever fail. What actually constitutes a prompt is a
    device flow, so this asserts none was started.
    """
    client = FakeMsal()
    auth = _auth(client, credential=_enrolled())

    assert auth.access_token() == ACCESS
    assert client.refreshed_with == [(REFRESH, sorted(GRAPH_RESOURCE_SCOPES))]
    assert client.initiated_with is None, "a silent refresh started a device flow"
    assert client.polled_with == [], "a silent refresh waited for a human"


def test_a_rotated_refresh_token_is_sealed_in_place_of_the_one_that_was_used():
    """AAD rotates on use, and the old token stops working.

    Skipping this write means the *next* start reports stale on a credential
    that was renewed seconds earlier — the failure is a whole process away from
    its cause.
    """
    client = FakeMsal(refresh_results=[_token(refresh_token=ROTATED)])
    auth = _auth(client, credential=_enrolled())

    auth.access_token()

    sealed = SealedCredential.decode(auth.store.read())
    assert sealed.refresh_token == ROTATED
    assert sealed.home_account_id == ACCOUNT, "rotation must not lose the account"


def test_a_rejected_refresh_token_reports_stale_and_not_unreachable():
    """The acceptance criterion: two states an operator must be able to tell apart."""
    client = FakeMsal(
        refresh_results=[
            {"error": "invalid_grant", "error_description": "the token has expired"}
        ]
    )
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(CredentialStale) as refused:
        auth.access_token()

    assert not isinstance(refused.value, GraphUnreachable)
    probe = auth.check_health()
    assert probe.health is Health.FAILING
    assert "stale" in probe.detail
    assert "network" in probe.remediation, "the remedy must exclude the other state"


def test_conditional_access_is_reported_as_needing_interaction_not_as_staleness():
    """Three remedies, three states.

    AAD delivers conditional access as `invalid_grant` with a `suberror`. Reading
    the code alone collapses it into expiry, and the PM is sent to re-issue a
    token that was never the problem.
    """
    client = FakeMsal(
        refresh_results=[
            {
                "error": "invalid_grant",
                "suberror": "basic_action",
                "error_description": "AADSTS50076: multi-factor authentication required",
            }
        ]
    )
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(InteractionRequired) as refused:
        auth.access_token()

    assert not isinstance(refused.value, CredentialStale)
    assert "conditional access" in str(refused.value)


def test_an_unreachable_token_endpoint_is_reported_as_unreachable():
    client = FakeMsal(raises=OSError("no route to host"))
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(GraphUnreachable) as refused:
        auth.access_token()

    assert not isinstance(refused.value, CredentialStale)
    assert "not known" in str(refused.value) or "not the same" in str(refused.value)


def test_an_error_code_pm_ai_has_no_remedy_for_is_not_forced_into_one():
    """An unmapped code lands on the base class, saying so.

    Mapping it onto whichever of the five looks closest is how an app-registration
    problem gets reported as an expired credential.
    """
    client = FakeMsal(refresh_results=[{"error": "invalid_client"}])
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(GraphAuthError) as refused:
        auth.access_token()

    assert type(refused.value) is GraphAuthError
    assert "invalid_client" in str(refused.value)


# ── The set comparison, on every acquisition ─────────────────────────────────


def test_partial_consent_is_refused_rather_than_degraded():
    """Six of seven produces a connector that will not start, on purpose.

    The alternative — run with what was granted and fail per-resource — is a
    connector whose health depends on which endpoint you happen to hit, which is
    the coverage claim AD-8a exists to stop.
    """
    partial = " ".join(sorted(GRAPH_RESOURCE_SCOPES - {"Chat.Read"}))
    client = FakeMsal(device_result=_token(scope=partial))
    auth = _auth(client)

    with pytest.raises(AuthDeclined) as refused:
        auth.sign_in(lambda message: None)

    assert "Chat.Read" in str(refused.value)
    assert "6 of 7" in str(refused.value)


def test_a_missing_online_meetings_read_is_named_at_acquisition():
    """The acceptance criterion the spike wrote.

    Without this scope the meeting lookup returns `403 Forbidden "Insufficient
    permissions"` — the same status as the tenant-level transcript switch — so a
    connector that started anyway would report "this tenant has disabled
    transcripts" when the truth is that nobody ever asked for the permission.
    """
    partial = " ".join(sorted(GRAPH_RESOURCE_SCOPES - {"OnlineMeetings.Read"}))
    client = FakeMsal(refresh_results=[_token(scope=partial)])
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(AuthDeclined) as refused:
        auth.access_token()

    assert "OnlineMeetings.Read" in str(refused.value)
    assert "transcripts switched off" in str(refused.value), (
        "the refusal must name the misdiagnosis it exists to prevent"
    )


def test_scopes_narrowed_after_enrolment_are_caught_on_a_later_acquisition():
    """Enforced on *every* acquisition, not only at enrolment.

    An administrator revoking a permission weeks later is the case a
    check-at-enrolment design cannot see at all.
    """
    narrowed = " ".join(sorted(GRAPH_RESOURCE_SCOPES - {"ChannelMessage.Read.All"}))
    client = FakeMsal(refresh_results=[_token(), _token(scope=narrowed)])
    auth = _auth(client, credential=_enrolled())

    assert auth.access_token() == ACCESS
    with pytest.raises(AuthDeclined) as refused:
        auth.access_token(force_refresh=True)
    assert "ChannelMessage.Read.All" in str(refused.value)


def test_a_token_response_declaring_no_scopes_is_refused():
    """Silence is not consent, and an unverified grant is not a grant."""
    client = FakeMsal(refresh_results=[_token(scope="")])
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(AuthDeclined) as refused:
        auth.access_token()

    assert "no scopes" in str(refused.value)


# ── The access token in hand ─────────────────────────────────────────────────


def test_a_live_access_token_is_reused_and_a_forced_refresh_is_not():
    """The mid-harvest 401 row, from the auth side.

    A page that 401s on a credential valid when the walk started needs a fresh
    token without the harvest being declared stale — so `force_refresh` has to
    reach the provider even though the cached token still looks live.
    """
    client = FakeMsal(refresh_results=[_token(), _token(access_token="a-second-token")])
    auth = _auth(client, credential=_enrolled())

    assert auth.access_token() == ACCESS
    assert auth.access_token() == ACCESS
    assert len(client.refreshed_with) == 1, "a live token was thrown away"

    assert auth.access_token(force_refresh=True) == "a-second-token"
    assert len(client.refreshed_with) == 2


def test_expiry_is_judged_with_a_margin_and_from_the_relative_lifetime():
    """A skewed wall clock cannot hand out a dead token.

    The arithmetic is `now() + expires_in` inside one process, so a constant
    offset cancels; the margin covers the rest — a clock that moves mid-process,
    and the flight time of the request the token is about to be used on.
    """
    moving = [NOW]
    client = FakeMsal(refresh_results=[_token(expires_in=3600), _token()])
    auth = _auth(client, credential=_enrolled(), now=lambda: moving[0])

    auth.access_token()
    moving[0] = NOW + timedelta(seconds=3600) - EXPIRY_MARGIN
    auth.access_token()

    assert len(client.refreshed_with) == 2, (
        "a token inside the expiry margin was handed out as live"
    )


def test_a_skewed_local_clock_is_reported_as_its_own_state():
    """Not stale, not unreachable — the remedy is fixing the clock.

    Measured against the id token's `iat`, which is what the provider's clock
    said when it minted this token. An unmeasured skew is reported as nothing at
    all rather than as zero.
    """
    adrift = NOW + CLOCK_SKEW_TOLERANCE + timedelta(hours=2)
    client = FakeMsal(
        refresh_results=[
            _token(id_token_claims={"oid": "an-object-id", "tid": "a-tenant-id",
                                    "iat": int(NOW.timestamp())})
        ]
    )
    auth = _auth(client, credential=_enrolled(), now=lambda: adrift)

    probe = auth.check_health()

    assert probe.health is Health.WARNING
    assert "clock" in probe.detail
    assert "Fix the clock" in probe.remediation


def test_a_clock_ahead_of_the_provider_is_reported_in_readable_units():
    """`abs()` in the sentence, not only in the comparison.

    A machine *behind* the provider gives a positive `timedelta` and reads
    fine; one ahead gives a negative, which `str()` renders as "-1 day,
    21:55:00" — an operator has to do modular arithmetic to learn their clock is
    two hours and five minutes fast.
    """
    ahead = NOW + timedelta(hours=2, minutes=5)
    client = FakeMsal(
        refresh_results=[
            _token(id_token_claims={"oid": "an-object-id", "tid": "a-tenant-id",
                                    "iat": int(NOW.timestamp())})
        ]
    )
    auth = _auth(client, credential=_enrolled(), now=lambda: ahead)

    probe = auth.check_health()

    assert probe.health is Health.WARNING
    assert "-1 day" not in probe.detail, "a negative timedelta reached the operator"
    assert "2:05:00" in probe.detail
    assert "ahead of" in probe.detail


def test_a_healthy_credential_with_no_measurable_skew_reports_ok():
    """No `iat` at all — the genuinely *unmeasured* case.

    This row supplied `iat == NOW` and called it unmeasurable, which is a
    measured skew of exactly zero: the opposite state, and one that passes
    whether or not the adapter can tell "no reading" from "a reading of zero".
    """
    client = FakeMsal(
        refresh_results=[_token(id_token_claims={"oid": "an-object-id", "tid": "a-tenant-id"})]
    )
    auth = _auth(client, credential=_enrolled())

    assert auth.check_health().health is Health.OK
    assert auth._skew is None, "an unmeasured skew was recorded as a measurement"


def test_a_measured_skew_inside_the_tolerance_still_reports_ok():
    client = FakeMsal(
        refresh_results=[
            _token(id_token_claims={"oid": "an-object-id", "tid": "a-tenant-id",
                                    "iat": int(NOW.timestamp())})
        ]
    )
    auth = _auth(client, credential=_enrolled())

    assert auth.check_health().health is Health.OK
    assert auth._skew == timedelta(0)


def test_a_fixed_clock_clears_the_skew_warning():
    """The reading is replaced on every acquisition, including with "no reading".

    Assigned only when an `iat` was present, a `WARNING` measured once outlived
    the repair: every later response without an `iat` left the stale reading in
    place, so an operator who fixed their clock kept being told to fix it.
    """
    adrift = NOW + CLOCK_SKEW_TOLERANCE + timedelta(hours=2)
    claims = {"oid": "an-object-id", "tid": "a-tenant-id"}
    client = FakeMsal(
        refresh_results=[
            _token(id_token_claims={**claims, "iat": int(NOW.timestamp())}),
            _token(id_token_claims=claims),
        ]
    )
    auth = _auth(client, credential=_enrolled(), now=lambda: adrift)

    assert auth.check_health().health is Health.WARNING
    assert auth.check_health().health is Health.OK, (
        "a skew reading nothing re-measured outlived the clock being fixed"
    )


def test_an_uninterpretable_issued_at_is_an_unmeasured_skew_and_not_a_crash():
    """An `iat` no calendar can hold used to escape as `OverflowError`/`OSError`.

    Neither is a `GraphAuthError`, so it went past every caller's `except` and
    reached `check_health`'s catch-all as "a bug in pm-ai" — for a provider
    response with one implausible field in it.
    """
    client = FakeMsal(
        refresh_results=[
            _token(id_token_claims={"oid": "an-object-id", "tid": "a-tenant-id",
                                    "iat": 1e30})
        ]
    )
    auth = _auth(client, credential=_enrolled())

    assert auth.access_token() == ACCESS
    assert auth._skew is None
    assert auth.check_health().health is Health.OK


# ── The MSAL account cache ───────────────────────────────────────────────────


def test_the_enrolled_account_is_selected_explicitly_when_the_cache_holds_several():
    """Never `accounts[0]`.

    A laptop where the PM has also signed a personal account in is the ordinary
    case, and refreshing the wrong identity harvests the wrong calendar.
    """
    client = FakeMsal(
        accounts=[
            {"home_account_id": "somebody-else.another-tenant"},
            {"home_account_id": ACCOUNT},
        ],
        silent_result=_token(),
    )
    auth = _auth(client, credential=_enrolled())

    assert auth.access_token() == ACCESS
    assert client.silent_calls[0]["account"]["home_account_id"] == ACCOUNT


def test_an_ambiguous_account_cache_is_refused():
    """Two accounts, neither of them the enrolled one, is not a token to guess at."""
    client = FakeMsal(
        accounts=[
            {"home_account_id": "somebody-else.another-tenant"},
            {"home_account_id": "a-third-party.a-third-tenant"},
        ]
    )
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(CredentialStale) as refused:
        auth.access_token()

    assert "cannot be identified" in str(refused.value)


def test_an_empty_account_cache_is_a_fresh_process_and_not_an_ambiguity():
    """MSAL's cache is per-process, so every daemon start begins with none.

    Refusing here would refuse every start; the sealed refresh token is exactly
    what that state is for.
    """
    client = FakeMsal(accounts=[])
    auth = _auth(client, credential=_enrolled())

    assert auth.access_token() == ACCESS
    assert client.silent_calls == []
    assert client.refreshed_with == [(REFRESH, sorted(GRAPH_RESOURCE_SCOPES))]


# ── Concurrent refresh ───────────────────────────────────────────────────────


class RotatingStore:
    """A sealed store another process rotates between two reads of it.

    The race the claim exists for: this process reads token T, another process
    refreshes first, and AAD retires T on use. This one's acquisition is then
    rejected for a credential that is not actually stale — somebody else's
    success made it obsolete. The loser must re-read, see a value that changed
    underneath it, and retry with the winner's token.

    `after` is returned from the second read onward, which is what puts the
    rotation *between* the acquisition and the re-read rather than before both.

    `exclusive` **serialises for real** — it is a re-entrancy-detecting lock
    rather than a no-op, because the earlier no-op was what let the retry branch
    look reachable. With the claim held across the acquisition, as the adapter
    used to hold it, a second read inside one claim can only ever return the
    value the first did, so the retry proved nothing about a store that actually
    locks. Every read and write records which claim it happened under, so a
    write outside one is a failure rather than an invisible pass.
    """

    def __init__(self, before: str, after: str) -> None:
        self.value = before
        self._after = after
        self.reads = 0
        self.entered = 0
        self.held = False
        self.writes: list[str] = []
        self.unclaimed: list[str] = []

    def read(self) -> str | None:
        self.reads += 1
        if not self.held:
            self.unclaimed.append("read")
        if self.reads > 1:
            self.value = self._after
        return self.value

    def write(self, credential: str) -> None:
        if not self.held:
            self.unclaimed.append("write")
        self.writes.append(credential)
        self.value = credential

    @contextmanager
    def exclusive(self):
        assert not self.held, (
            "the claim was taken while already held — a real sealed-store lock "
            "would deadlock here, and holding it across the network call is what "
            "makes the concurrent-refresh retry unreachable"
        )
        self.entered += 1
        self.held = True
        try:
            yield
        finally:
            self.held = False


def test_the_loser_of_a_concurrent_refresh_retries_before_reporting_stale():
    """The matrix's concurrent-refresh row, against a store that really serialises.

    The retry is only reachable because the claim is released around the
    acquisition: held across it, the re-read happens inside the same claim as the
    first read and can never see a different value, so the branch was dead code
    under any store whose `exclusive()` does what its docstring says.
    """
    client = FakeMsal(
        refresh_results=[
            {"error": "invalid_grant", "error_description": "already redeemed"},
            _token(refresh_token="a-third-refresh-token"),
        ]
    )
    store = RotatingStore(before=_enrolled(), after=_enrolled("the-winners-token"))
    auth = GraphDeviceCodeAuth(
        client_id=CLIENT_ID,
        store=store,
        client_factory=lambda client_id, authority: client,
        now=lambda: NOW,
    )

    assert auth.access_token() == ACCESS
    assert [token for token, _ in client.refreshed_with] == [REFRESH, "the-winners-token"], (
        "the loser reported stale instead of retrying with the rotated token"
    )
    assert store.unclaimed == [], f"the store was touched outside a claim: {store.unclaimed}"


def test_the_rotated_token_is_written_under_the_claim():
    client = FakeMsal(refresh_results=[_token(refresh_token=ROTATED)])
    store = RotatingStore(before=_enrolled(), after=_enrolled())
    auth = GraphDeviceCodeAuth(
        client_id=CLIENT_ID,
        store=store,
        client_factory=lambda client_id, authority: client,
        now=lambda: NOW,
    )

    auth.access_token()

    assert store.unclaimed == [], f"the store was touched outside a claim: {store.unclaimed}"
    assert store.entered >= 2, "the read and the write-back were not each claimed"
    assert SealedCredential.decode(store.writes[-1]).refresh_token == ROTATED


def test_the_rotated_token_is_sealed_even_when_the_scope_comparison_refuses():
    """The response that both rotated the credential and failed the set comparison.

    AAD retires a refresh token the moment it is used, so an acquisition that
    succeeded at the provider and then failed `_assert_granted` still carried the
    only live credential this machine has. Discarding it left the store holding a
    dead token, and the connector reported stale forever — including after an
    administrator granted the missing permission back, because by then there was
    nothing left to refresh with.
    """
    narrowed = " ".join(sorted(GRAPH_RESOURCE_SCOPES - {"Chat.Read"}))
    client = FakeMsal(refresh_results=[_token(refresh_token=ROTATED, scope=narrowed)])
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(AuthDeclined):
        auth.access_token()

    assert SealedCredential.decode(auth.store.read()).refresh_token == ROTATED, (
        "the rotated credential was discarded, so the store now holds a token "
        "AAD has already retired"
    )


def test_a_store_that_cannot_seal_the_rotated_token_says_so_as_a_typed_refusal():
    """Custody failing at the worst moment is still a refusal, not a raw traceback."""

    class WriteRefused:
        def read(self):
            return _enrolled()

        def write(self, credential):
            raise RuntimeError(f"the keychain refused while holding {REFRESH}")

        def exclusive(self):
            return nullcontext()

    auth = GraphDeviceCodeAuth(
        client_id=CLIENT_ID,
        store=WriteRefused(),
        client_factory=lambda client_id, authority: FakeMsal(
            refresh_results=[_token(refresh_token=ROTATED)]
        ),
        now=lambda: NOW,
    )

    with pytest.raises(GraphAuthError) as refused:
        auth.access_token()

    assert "could not be sealed" in str(refused.value)
    assert "custody" in str(refused.value)
    rendered = f"{refused.value}{refused.value.args}{refused.value.__cause__!r}"
    assert REFRESH not in rendered and ROTATED not in rendered, (
        "a custody failure that quoted the credential was relayed verbatim"
    )
    assert auth.check_health().health is Health.FAILING


# ── The health probe ─────────────────────────────────────────────────────────


def test_nothing_enrolled_reports_absent_and_not_failing():
    """A fresh install is not a broken machine."""
    auth = _auth(FakeMsal(), credential=None)

    probe = auth.check_health()

    assert probe.health is Health.ABSENT
    assert "connector add graph" in probe.remediation


def test_a_blank_stored_credential_reports_absent_and_not_stale():
    """A half-finished enrolment is a setup step outstanding, not a bad token.

    An empty or whitespace string decoded as a malformed document and reported
    "the sealed credential is not the document this adapter writes" — a
    corruption message for a machine nobody had finished setting up, which is the
    same conflation `GitLabConnectorAdapter` already refuses for its own field.
    """
    for blank in ("", "   ", "\n"):
        auth = _auth(FakeMsal(), credential=blank)
        probe = auth.check_health()
        assert probe.health is Health.ABSENT, f"{blank!r} did not read as absent"
        assert "connector add graph" in probe.remediation
        with pytest.raises(CredentialStale) as refused:
            auth.access_token()
        assert "nothing to refresh" in str(refused.value)


def test_the_probe_asks_the_provider_rather_than_this_process_s_memory():
    """A warm cache is not evidence about a credential.

    `check_health` asked with `force_refresh=False`, so a daemon that had
    refreshed a minute earlier reported `OK` for the rest of the access token's
    life — up to `expires_in - EXPIRY_MARGIN`, fifty-five minutes on AAD's
    default — on a credential that had since been revoked.
    """
    client = FakeMsal(
        refresh_results=[
            _token(),
            {"error": "invalid_grant", "error_description": "the token was revoked"},
        ]
    )
    auth = _auth(client, credential=_enrolled())

    assert auth.access_token() == ACCESS
    probe = auth.check_health()

    assert probe.health is Health.FAILING, (
        "a cached access token reported a revoked credential as healthy"
    )
    assert "stale" in probe.detail
    assert len(client.refreshed_with) == 2, "the probe never asked the provider"


def test_an_abandoned_sign_in_reaching_the_probe_is_not_reported_as_a_bug():
    """`AuthTimedOut` is a mapped state, and fell through to the catch-all.

    Reachable from a refresh rather than only from a sign-in: AAD answers
    `expired_token` on a refresh token whose flow was never completed. The
    operator was told to report a bug in pm-ai for a sign-in somebody walked away
    from.
    """
    client = FakeMsal(refresh_results=[{"error": "expired_token"}])
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(AuthTimedOut):
        auth.access_token()
    probe = auth.check_health()

    assert probe.health is Health.FAILING
    assert "bug in pm-ai" not in probe.detail
    assert "never completed" in probe.detail
    assert "finish the sign-in" in probe.remediation


def test_an_unreachable_provider_reports_failing_distinctly_from_stale():
    auth = _auth(FakeMsal(raises=OSError("no route to host")), credential=_enrolled())

    probe = auth.check_health()

    assert probe.health is Health.FAILING
    assert "could not reach" in probe.detail
    assert "network" in probe.remediation
    assert "sign in again" not in probe.remediation.casefold()


def test_the_probe_reports_and_never_raises():
    """Every refusal the adapter can produce comes back as a row.

    One broken connector must not hide three others, which is the rule the whole
    `Probe` type exists to encode.
    """
    for result in (
        {"error": "invalid_grant"},
        {"error": "invalid_grant", "suberror": "basic_action"},
        {"error": "invalid_client"},
        _token(scope="Calendars.Read"),
        _token(access_token=None),
    ):
        auth = _auth(FakeMsal(refresh_results=[result]), credential=_enrolled())
        probe = auth.check_health()
        assert probe.health is Health.FAILING
        assert probe.remediation, f"no remedy offered for {result}"


def test_an_unreadable_sealed_store_is_not_reported_as_a_fresh_install():
    """Could-not-ask is not the same fact as nothing-stored.

    Answering `ABSENT` when the keychain is locked presents a fully configured
    machine as one nobody has set up.
    """

    class Locked:
        def read(self):
            raise RuntimeError("the keychain refused to answer")

        def write(self, credential):  # pragma: no cover - never reached
            raise AssertionError("write on a store that cannot be read")

        def exclusive(self):  # pragma: no cover - never reached
            raise AssertionError("claim on a store that cannot be read")

    auth = GraphDeviceCodeAuth(
        client_id=CLIENT_ID,
        store=Locked(),
        client_factory=lambda client_id, authority: FakeMsal(),
        now=lambda: NOW,
    )

    probe = auth.check_health()

    assert probe.health is Health.FAILING
    assert "custody" in probe.remediation


# ── The credential never appears ─────────────────────────────────────────────


def test_no_refusal_carries_the_refresh_token():
    """Not in the message, not in `args`, not in the chained cause.

    A refusal that quotes the token puts it in the operator's scrollback and in
    every bug report that follows.
    """
    refusals = []
    for result in (
        {"error": "invalid_grant", "error_description": f"token {REFRESH} is expired"},
        {"error": "invalid_grant", "suberror": "basic_action"},
        {"error": "invalid_client"},
        _token(scope="Calendars.Read"),
    ):
        auth = _auth(FakeMsal(refresh_results=[result]), credential=_enrolled())
        with pytest.raises(GraphAuthError) as refused:
            auth.access_token()
        refusals.append(refused.value)

    for refusal in refusals:
        rendered = f"{refusal}{refusal.args}{refusal.__cause__!r}"
        assert REFRESH not in rendered, f"a refusal quoted the refresh token: {refusal}"


def test_a_provider_that_echoes_only_part_of_the_token_is_still_withheld():
    """The `_ECHO_WINDOW` case, which the whole-token rows cannot exercise.

    Most APIs identify a credential back to you by its first characters — "token
    glpat-not-a… is expired". A whole-string check passes that and prints half
    the secret, which is why the comparison slides an eight-character window; and
    every existing row embedded the *entire* token, so a plain `in` check would
    have satisfied them all and the window was never measured.
    """
    fragment = REFRESH[:12]
    assert fragment not in _WITHHELD and len(fragment) > 8
    client = FakeMsal(
        refresh_results=[
            {
                "error": "invalid_grant",
                "error_description": f"the token {fragment}... has expired",
            }
        ]
    )
    auth = _auth(client, credential=_enrolled())

    with pytest.raises(CredentialStale) as refused:
        auth.access_token()

    rendered = f"{refused.value}{refused.value.args}"
    assert fragment not in rendered, "a truncated echo of the credential was printed"
    assert _WITHHELD in rendered, "the withheld marker must say why the message is gone"


def test_a_provider_that_echoes_the_device_code_is_withheld_too():
    """The other secret this adapter holds, and the only untested redaction path.

    The device code is a bearer of the sign-in for as long as the flow is live —
    anyone who reads it out of a log can complete the PM's sign-in — so `sign_in`
    passes it to `_refuse` as the secret to check the provider's sentence
    against. Nothing exercised that argument.
    """
    code = "a-device-code-long-enough-to-window"
    client = FakeMsal(
        flow=_flow(device_code=code),
        device_result={
            "error": "expired_token",
            "error_description": f"the code {code[:15]} was never used",
        },
    )
    auth = _auth(client)

    with pytest.raises(AuthTimedOut) as refused:
        auth.sign_in(lambda message: None)

    rendered = f"{refused.value}{refused.value.args}"
    assert code[:15] not in rendered
    assert _WITHHELD in rendered


def test_a_malformed_sealed_credential_is_refused_without_quoting_it():
    auth = _auth(FakeMsal(), credential="not a credential document")

    with pytest.raises(CredentialStale) as refused:
        auth.access_token()

    assert "not a credential document" not in str(refused.value)
    assert "again" in str(refused.value)


# ── Conformance ──────────────────────────────────────────────────────────────


def test_the_adapter_satisfies_the_port():
    from pm_ai.ports import GraphAuthPort

    assert isinstance(_auth(FakeMsal()), GraphAuthPort)


def test_the_store_default_satisfies_its_protocol():
    from pm_ai.connectors.graph.auth import RefreshTokenStore

    assert isinstance(InMemoryRefreshTokenStore(), RefreshTokenStore)


def test_custody_has_no_default_and_must_be_chosen():
    """`InMemoryRefreshTokenStore` was the `default_factory`, so silence chose it.

    Its own docstring says it is not a production store and survives no restart,
    which made "wired without thinking about custody" indistinguishable from
    "wired for a test" — and produced a daemon that re-prompted for a sign-in on
    every start. There is no safe default for where a credential lives.
    """
    with pytest.raises(TypeError) as refused:
        GraphDeviceCodeAuth(client_id=CLIENT_ID)  # type: ignore[call-arg]

    assert "store" in str(refused.value)


def test_the_declared_scope_set_cannot_be_overridden_by_a_caller():
    """One place, enforced by the constructor rather than only by a constant test.

    `scopes` was a field defaulting to `GRAPH_SCOPES`, so a call site could hand
    in its own set — and `_assert_granted` compares the grant against
    `self.scopes`, so the partial-consent refusal was switchable off from
    outside while every module-level assertion about `GRAPH_SCOPES` still passed.
    """
    with pytest.raises(TypeError):
        GraphDeviceCodeAuth(  # type: ignore[call-arg]
            client_id=CLIENT_ID,
            store=InMemoryRefreshTokenStore(),
            scopes=frozenset({"Calendars.Read"}),
        )
    assert _auth(FakeMsal()).scopes == GRAPH_SCOPES


def test_an_unimportable_msal_is_a_typed_refusal_and_not_a_traceback(monkeypatch):
    """The lazy import's whole purpose, exercised without the network.

    `None` in `sys.modules` makes `import msal` raise `ImportError` — the shape a
    half-installed package produces, and one `except ModuleNotFoundError` alone
    would let out as a raw traceback from inside a probe that is required never
    to raise.

    Written this way rather than by leaning on `msal` being absent from this
    environment, deliberately: the same test then holds under `uv sync --extra
    runtime`, and it never constructs a real client — which is what keeps "no
    test in this file opens a socket" true in both environments rather than only
    in the one it happened to be written in.
    """
    import sys

    monkeypatch.setitem(sys.modules, "msal", None)

    with pytest.raises(GraphAuthError) as refused:
        GraphDeviceCodeAuth(
            client_id=CLIENT_ID, store=InMemoryRefreshTokenStore()
        ).sign_in(lambda message: None)

    assert "could not be imported" in str(refused.value)
    assert "uv sync --extra runtime" in str(refused.value)


def test_a_probe_reports_even_when_the_adapter_itself_breaks():
    """Never raises means never, including on a bug in this module.

    One connector's broken probe must not take the report down with it, which is
    the rule `Probe` exists to encode and the reason the catch-all in
    `check_health` is a contract rather than a shrug.

    Broken *here* means the adapter's own wiring, not MSAL: a clock that raises
    is nothing a provider said. An MSAL call that fails is a different fact, and
    the row below is what keeps the two apart.
    """

    def broken_clock():
        raise RuntimeError("a bug in the adapter, not a verdict")

    auth = _auth(FakeMsal(), credential=_enrolled(), now=broken_clock)

    probe = auth.check_health()

    assert probe.health is Health.FAILING
    assert "bug in pm-ai" in probe.detail


def test_an_msal_call_that_fails_is_unreachable_rather_than_a_pm_ai_bug():
    """`get_accounts` and the client constructor are MSAL, not this module.

    Both sat outside the guard every other MSAL touch runs under, so a real
    failure in either surfaced untyped from `access_token` and as "a bug in
    pm-ai" from `check_health` — an operator told to file a report because their
    network was down. The previous version of the row above asserted exactly that
    mislabelling and so locked it in.
    """

    class Exploding:
        def get_accounts(self, username=None):
            raise RuntimeError("MSAL could not read its token cache")

    auth = _auth(Exploding(), credential=_enrolled())
    with pytest.raises(GraphUnreachable):
        auth.access_token()
    probe = auth.check_health()
    assert probe.health is Health.FAILING
    assert "bug in pm-ai" not in probe.detail
    assert "could not reach" in probe.detail

    def refusing_factory(client_id, authority):
        raise OSError("no route to the authority")

    unbuildable = GraphDeviceCodeAuth(
        client_id=CLIENT_ID,
        store=InMemoryRefreshTokenStore(_enrolled()),
        client_factory=refusing_factory,
        now=lambda: NOW,
    )
    with pytest.raises(GraphUnreachable):
        unbuildable.access_token()
    assert "bug in pm-ai" not in unbuildable.check_health().detail
