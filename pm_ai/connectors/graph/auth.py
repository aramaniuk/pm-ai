"""Microsoft Graph delegated auth by device code (story 33a, AD-1 class H).

The PM signs in once, at a URL, with a code this adapter prints. No client
secret sits on the laptop and no redirect URI has to be hosted, which is what
makes the flow fit a local-first single-PM tool. What it does *not* avoid is
tenant-admin consent: two of the seven permissions below normally require an
administrator, and that is a one-time app-registration prerequisite rather than
a per-sign-in step.

## Why the adapter is here and not in `pm_ai.platform`

Token **acquisition** talks HTTP to the Microsoft token endpoint, and
`.importlinter`'s `http-confined-to-adapters` states the intent as "only inbound
connectors and outbound skills may speak HTTP at all". Token **custody** is the
other half and stays in `platform`: the master key behind `KeychainPort`, and
the sealed store the master key opens.

That contract named `httpx`, `requests` and `aiohttp` and MSAL reaches
`requests` transitively, so until this slice the misplacement the paragraph
above argues against would have *passed* the gate. `msal` joins the contract in
the same commit as this module, because a violation that passes the gate is
worse than one that fails.

## What is imported lazily, and why that is not tidiness

`msal` is declared in the `runtime` extra and is deliberately not installed in
the test environment. The suite turns a `ModuleNotFoundError` at import time
into a *skipped* test, and a skipped test reads as coverage in a green run —
the one outcome an auth test exists to rule out. Imported inside the call, a
missing package is a typed refusal at the moment it matters and this module
imports fine without the extra. The same reasoning `pm_ai.platform.keychain`
records for `keyring`.

## The declared scope set, and the one that reaches MSAL

`GRAPH_SCOPES` is the set the PM consents to: seven resource permissions plus
`offline_access`, which is not a resource permission and is the only reason a
silent refresh is possible at all. `_resource_scopes()` is what is passed to
MSAL, and it omits `offline_access` — **measured against msal 1.38.0**, which
refuses it as a user-provided scope ("You cannot use any scope value that is
reserved. ... The reserved list: ['offline_access', 'openid', 'profile']") and
adds all three itself. So the declared set and the requested list differ by
construction, and `offline_access` is verified the only way it can be: a
sign-in that comes back without a refresh token did not get it.

## Partial consent is refused, not degraded

The granted set is compared against the declared set on **every** acquisition.
An administrator who grants six of seven produces a connector that refuses
entirely rather than one that quietly works for calendars — that is AD-8a's
rule, and it costs capability on purpose. A connector whose health depends on
which endpoint you happen to hit is not diagnosable; one that will not start is
diagnosable in one line.

The direction that matters most is *omission*. `OnlineMeetings.Read` was
missing from this set until a live tenant answered on 2026-09-06, and its
absence surfaces as `403 Forbidden "Insufficient permissions"` on the meeting
lookup — the same status as the tenant-level transcript switch. A connector
missing it reports "this tenant has disabled transcripts" when the truth is "we
never asked for the right permission". So the comparison names the missing
scope at acquisition rather than leaving it to be misdiagnosed later.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn, Protocol, runtime_checkable

from pm_ai.domain.health import Health, Probe

__all__ = [
    "AuthDeclined",
    "AuthTimedOut",
    "CLOCK_SKEW_TOLERANCE",
    "CredentialStale",
    "EXPIRY_MARGIN",
    "GRAPH_RESOURCE_SCOPES",
    "GRAPH_SCOPES",
    "GraphAuthError",
    "GraphDeviceCodeAuth",
    "GraphUnreachable",
    "InteractionRequired",
    "InMemoryRefreshTokenStore",
    "OFFLINE_ACCESS",
    "RefreshTokenStore",
    "SealedCredential",
]


# ── The scope set, declared once ─────────────────────────────────────────────

GRAPH_RESOURCE_SCOPES = frozenset(
    {
        # 33b — the calendar view every meeting record starts from.
        "Calendars.Read",
        # 33d — chat messages, and channel message *content*.
        "Chat.Read",
        "ChannelMessage.Read.All",
        # 33d again — reading a channel message does not permit *finding* one.
        # Enumeration is a separate grant, measured as two separate refusals.
        # Declaring these forecloses 33d's alternative (explicit team and
        # channel ids in configuration), deliberately.
        "Team.ReadBasic.All",
        "Channel.ReadBasic.All",
        # 33e — the transcripts, and resolving a join URL to the meeting that
        # holds them. The second is not optional: without it the lookup 403s
        # before the transcript endpoint is ever reached.
        "OnlineMeetingTranscript.Read.All",
        "OnlineMeetings.Read",
    }
)
"""The seven delegated resource permissions, for the whole connector family.

Not for this wave — four of these belong to 33d and two to 33e, and none is
called in wave 1. They are declared here because consent happens **once**, at
enrolment, and the partial-consent rule below turns a scope added later into a
forced re-enrolment rather than a degradation. One consent, asked for once.

The cost is real and accepted: an app registration must carry all seven before
anyone can enrol at all.
"""

OFFLINE_ACCESS = "offline_access"
"""Not a resource permission — the grant that makes silent refresh possible."""

GRAPH_SCOPES = GRAPH_RESOURCE_SCOPES | {OFFLINE_ACCESS}
"""What the PM is asked to consent to, asserted as a set by this slice's tests.

A set rather than a list so that a silent *addition* — someone adding
`Mail.Read` for a feature and widening the consent prompt — and a silent
*omission* both fail. The omission direction is the one a live tenant actually
exercised.
"""


# ── Tolerances ───────────────────────────────────────────────────────────────

EXPIRY_MARGIN = timedelta(minutes=5)
"""How early a cached access token is treated as expired.

Judged from the provider's **relative** `expires_in` rather than from any
absolute timestamp it sends, which is what makes a laptop clock that is hours
off unable to hand out a dead token: the arithmetic is `now() + expires_in`
within one process, so a constant offset cancels. The margin covers the rest —
a clock that moves mid-process, and the flight time of the request the token is
about to be used on.
"""

CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)
"""How far this machine's clock may sit from the provider's before it is reported.

Skew is its own state, not a stale credential: the remedy is fixing the clock,
and telling the PM to sign in again would send them in a circle. Measured only
when the provider actually hands over a timestamp to compare against — an
unmeasured skew is reported as nothing at all rather than as zero.
"""


# ── Refusals ─────────────────────────────────────────────────────────────────


class GraphAuthError(Exception):
    """A Graph token could not be obtained, and the subclass says why.

    A base rather than five unrelated exceptions, so a caller that does not care
    which refusal it was can write one `except` — and so an error code Microsoft
    returns that pm-ai has no mapping for has a home that is honest about being
    unmapped, instead of being forced into whichever of the five looks closest.

    Carries no credential material, ever. Not in its message, not in its `args`,
    not in the traceback of whatever it was raised from.
    """


class CredentialStale(GraphAuthError):
    """The stored credential exists and the provider will not renew it.

    The remedy is a fresh interactive sign-in. Deliberately distinct from
    `GraphUnreachable`, which needs waiting, and from `InteractionRequired`,
    which needs a sign-in for a reason re-enrolment does not fix on its own.

    Also raised when nothing has been enrolled at all. The two states are
    different facts and the *health probe* keeps them apart — `ABSENT` versus
    `FAILING` — but the remedy for a caller that asked for a token is the same
    sentence, and inventing a sixth exception to say it twice would be a
    distinction with no consequence.
    """


class AuthTimedOut(GraphAuthError):
    """The device code expired before anyone signed in with it.

    Nothing is stored. The PM walked away, or never saw the code; the flow is
    started again from the beginning rather than resumed.
    """


class AuthDeclined(GraphAuthError):
    """Consent was refused, or granted for less than the declared set.

    Both are one refusal because the connector's answer is the same in both:
    it will not start. Naming the specific scope is what turns this from an
    unactionable "consent failed" into an app-registration change somebody can
    make.
    """


class GraphUnreachable(GraphAuthError):
    """The token endpoint never answered, so nothing is known about the credential.

    Unknown is not a verdict — the reading `ProbeUnreachable`,
    `KeychainUnavailable` and `VcsUnavailable` all take. Reporting an
    unreachable network as a stale credential sends the PM to re-enrol a token
    that is perfectly good.
    """


class InteractionRequired(GraphAuthError):
    """Conditional access wants a human at the sign-in, and no refresh will do.

    The third remedy, and the reason a two-state design was wrong: waiting does
    not fix it and neither does re-issuing a token. Someone has to complete a
    challenge — MFA, a compliant-device check — interactively.
    """


# ── What is sealed, and where ────────────────────────────────────────────────


@runtime_checkable
class RefreshTokenStore(Protocol):
    """Custody of the one credential this adapter holds, injected rather than reached.

    The adapter may not open a file, reach a keychain, or import the single
    writer — `pm_ai.connectors` may do none of those three. So the sealed store
    arrives as this shape and the composition root decides what backs it, which
    is the same arrangement `ScopePathPort` and `VcsPort` use one layer down.
    """

    def read(self) -> str | None:
        """The sealed credential, or `None` when nothing has been enrolled.

        `None` is an *answer* — the ordinary state of a machine nobody has
        finished setting up. A store that could not be read raises instead, and
        the difference is what keeps a locked keychain from presenting as a
        fresh install.
        """

    def write(self, credential: str) -> None:
        """Replace the sealed credential, after the provider rotated it."""

    def exclusive(self) -> AbstractContextManager[None]:
        """Serialise the read-modify-write around a refresh.

        AAD rotates the refresh token on use, so two processes that both read
        the old one and both write back leave one of them holding a token the
        provider has already retired. The read and the write have to be one
        step, exactly as the sealed store's own read-modify-write is.
        """


@dataclass(frozen=True, slots=True)
class SealedCredential:
    """What is stored for an enrolled Graph account: the token, and whose it is.

    Two fields rather than a bare token string, because MSAL's cache can hold
    several accounts and the enrolled one has to be identifiable *explicitly*.
    Taking `accounts[0]` is the shape that silently refreshes the wrong
    identity on a laptop where the PM has also signed a personal account in.

    Carried as one JSON string because story 8b's sealed store holds exactly one
    `credential` per connector instance. `home_account_id` is not secret; it
    travels with the token only so the two can never be separated.
    """

    refresh_token: str
    home_account_id: str

    def encode(self) -> str:
        """The single string story 8b seals. Sorted, so two runs agree."""
        return json.dumps(
            {"refresh_token": self.refresh_token, "home_account_id": self.home_account_id},
            sort_keys=True,
        )

    @classmethod
    def decode(cls, raw: str) -> SealedCredential:
        """Read one back, refusing anything that is not one.

        `CredentialStale` for every malformed shape, and deliberately not a
        `ValueError` or a `json.JSONDecodeError`: what a caller can do about a
        credential it cannot interpret is exactly what it can do about one the
        provider rejected, and the refusal must not quote the bytes it failed on.
        """
        try:
            document = json.loads(raw)
        except (TypeError, ValueError):
            raise CredentialStale(
                "the sealed Graph credential is not the document this adapter "
                "writes, so it cannot be used to refresh anything. Enrol the "
                "Graph connector again."
            ) from None
        if not isinstance(document, dict):
            raise CredentialStale(
                "the sealed Graph credential decoded to something that is not an "
                "object. Enrol the Graph connector again."
            )
        token = document.get("refresh_token")
        account = document.get("home_account_id")
        if not isinstance(token, str) or not token:
            raise CredentialStale(
                "the sealed Graph credential carries no refresh token, so there "
                "is nothing to refresh from. Enrol the Graph connector again."
            )
        if not isinstance(account, str) or not account:
            raise CredentialStale(
                "the sealed Graph credential does not say which account it "
                "belongs to, so the enrolled account cannot be identified among "
                "MSAL's cached ones. Enrol the Graph connector again."
            )
        return cls(refresh_token=token, home_account_id=account)


@dataclass
class InMemoryRefreshTokenStore:
    """The default custody: this process, and nowhere else.

    Not a production store and does not pretend to be one — it survives no
    restart. It exists so `sign_in` has somewhere to put the token it just
    obtained, in the same process, before enrolment seals the value it returns.
    A composition root that has the sealed store hands in one backed by it.

    `exclusive` is a no-op here because one process cannot race itself for this
    object; a sealed-store implementation is where the claim does work.
    """

    credential: str | None = None

    def read(self) -> str | None:
        return self.credential

    def write(self, credential: str) -> None:
        self.credential = credential

    def exclusive(self) -> AbstractContextManager[None]:
        return nullcontext()


# ── Error-code mapping ───────────────────────────────────────────────────────
#
# AAD answers with an `error` code and sometimes a `suberror`, and the mapping
# from those to a remedy is the whole of this slice's value. Kept as data rather
# than a chain of `if`s so a reader can see every code pm-ai claims to
# understand, and so an unrecognised one is visibly unrecognised.

_ABANDONED = frozenset({"authorization_pending", "slow_down", "expired_token", "code_expired"})
"""Terminal from `acquire_token_by_device_flow`, which means the flow expired.

Measured against msal 1.38.0: `authorization_pending` and `slow_down` are its
*retriable* errors, and its polling loop returns them only once the flow's own
`expires_at` has passed. So seeing one here is not throttling — it is a code
nobody used. Throttling never reaches this module: MSAL honours the interval
from the device-code response and adds RFC 8628's five seconds on `slow_down`,
which is why this adapter passes the flow object through untouched and supplies
no `exit_condition` that would shorten the wait.
"""

_DECLINED = frozenset({"access_denied", "consent_required"})

_INTERACTION = frozenset({"interaction_required", "login_required"})

_INTERACTION_SUBERRORS = frozenset(
    {
        "basic_action",
        "additional_action",
        "message_only",
        "consent_required",
        "user_password_expired",
        "bad_token",
    }
)
"""AAD's `suberror` on an `invalid_grant`, which is how conditional access arrives.

Read *before* the error code, because `invalid_grant` alone would otherwise be
read as expiry — collapsing the third remedy into the first, which is exactly
the defect the two-state design had.
"""

_STALE = frozenset({"invalid_grant", "invalid_token", "token_expired", "unauthorized_client"})

_WITHHELD = "[the provider's message is withheld: it quoted the credential]"

_ECHO_WINDOW = 8
"""How long a run of credential characters is enough to be worth not printing.

Eight, for the reason story 8b's `_redacted` uses eight: a provider that echoes
a *truncated* token — "token glpat-not-a… is expired", which is how most APIs
identify a credential back to you — passes a whole-string check and prints the
first half of the secret.
"""


def _without(text: str, secret: str | None) -> str:
    """`text`, unless the provider quoted `secret` into it.

    AAD's `error_description` is worth carrying through: the AADSTS code in it is
    what an operator actually searches for, and dropping it leaves a refusal
    nobody can act on. But it is *their* string, composed by a service pm-ai does
    not control, and one that quoted the token it was handed would put that token
    in this machine's scrollback and in every bug report that followed. So the
    check is here, in the module that owns the secret, rather than a hope about
    somebody else's formatting.

    Replaces the whole message rather than masking the match: a partial redaction
    still tells a reader how long the credential is and what it starts with.
    """
    if not secret or not text:
        return text
    folded = text.casefold()
    candidates = {secret.casefold()}
    if len(secret) > _ECHO_WINDOW:
        candidates |= {
            secret[index : index + _ECHO_WINDOW].casefold()
            for index in range(len(secret) - _ECHO_WINDOW + 1)
        }
    return _WITHHELD if any(c and c in folded for c in candidates) else text


# ── The adapter ──────────────────────────────────────────────────────────────


def _msal_public_client(client_id: str, authority: str) -> Any:
    """A real `msal.PublicClientApplication`, imported at call time.

    Public rather than confidential on purpose: a confidential client needs a
    secret, and a secret on a PM's laptop is the thing device code exists to
    avoid.
    """
    try:
        import msal
    except ImportError as missing:
        # `ImportError` rather than `ModuleNotFoundError`: a half-installed
        # package that raises while importing its own dependencies is the same
        # problem for a caller — pm-ai cannot ask Microsoft anything — and the
        # narrower catch would have let that one out as a raw traceback.
        raise GraphAuthError(
            f"the `msal` package could not be imported, so pm-ai cannot obtain a "
            f"Microsoft Graph token ({missing}). It is declared in the "
            f"`runtime` extra: install it with `uv sync --extra runtime`. This "
            f"is an incomplete installation rather than anything the provider "
            f"said, which is why it is not one of the five auth states."
        ) from missing
    return msal.PublicClientApplication(client_id, authority=authority)


@dataclass
class GraphDeviceCodeAuth:
    """`GraphAuthPort` over MSAL's device-code flow.

    Holds one enrolled account. Two Graph identities are two instances with two
    stores, for the same reason two GitLab projects are two connectors: an
    instance name is what a cursor, a coverage window and a credential are all
    keyed by.
    """

    client_id: str
    tenant: str = "organizations"
    """The authority segment. `organizations` is any work or school tenant.

    Not `common`, which also admits personal Microsoft accounts — none of the
    seven permissions means anything for one, so the sign-in would succeed and
    every later call would 403.
    """

    instance: str = "graph"
    store: RefreshTokenStore = field(default_factory=InMemoryRefreshTokenStore)
    scopes: frozenset[str] = GRAPH_SCOPES
    client_factory: Callable[[str, str], Any] = _msal_public_client
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    # In-process state. Not constructor arguments: a caller supplying a cached
    # access token would be supplying a claim about a provider it never asked.
    _app: Any = field(default=None, init=False, repr=False)
    _access_token: str | None = field(default=None, init=False, repr=False)
    _expires_at: datetime | None = field(default=None, init=False, repr=False)
    _skew: timedelta | None = field(default=None, init=False, repr=False)

    # ── The port ─────────────────────────────────────────────────────────────

    def sign_in(self, present: Callable[[str], None]) -> str:
        """Present a code and a URL, wait for the human, return what to seal.

        `present` is called with MSAL's own sentence, which already contains the
        code and the verification URL. Nothing here opens a browser or reads a
        password: the PM signs in wherever they already are, and this process
        only waits.

        The waiting is MSAL's loop, not one written here. It honours the
        `interval` the device-code response carried and backs off by RFC 8628's
        five seconds when the endpoint answers `slow_down` — so the flow object
        is handed back exactly as it arrived, with no `exit_condition` that
        would cut the poll short and no interval of this adapter's own. AD-9
        forbids a connector owning cadence; this is why that costs nothing here.

        Returns the credential story 8b seals. It also lands in this adapter's
        store, so `access_token` works in the same process before the enrolment
        that seals it has run.
        """
        app = self._application()
        flow = self._call(app.initiate_device_flow, self._resource_scopes())
        if "user_code" not in flow:
            self._refuse(flow, during="starting the sign-in")
        present(self._prompt(flow))
        result = self._call(app.acquire_token_by_device_flow, flow)
        if "error" in result:
            self._refuse(
                result, during="the sign-in", secret=str(flow.get("device_code") or "")
            )
        self._assert_granted(result)
        refresh_token = result.get("refresh_token")
        if not refresh_token:
            # `offline_access` cannot be compared as a string: MSAL refuses it
            # as a user-provided scope and AAD does not echo it back in the
            # granted list. A sign-in with no refresh token *is* the measurement.
            raise AuthDeclined(
                f"the sign-in returned an access token and no refresh token, so "
                f"{OFFLINE_ACCESS!r} was not granted. Without it pm-ai would "
                f"have to prompt for a sign-in every hour, which is not a "
                f"connector. Grant it on the app registration and enrol again."
            )
        sealed = SealedCredential(
            refresh_token=refresh_token,
            home_account_id=self._enrolling_account(app, result),
        )
        credential = sealed.encode()
        with self.store.exclusive():
            self.store.write(credential)
        self._adopt(result)
        return credential

    def access_token(self, *, force_refresh: bool = False) -> str:
        """A bearer token, refreshed silently. Never prompts.

        A cached token is returned while it is comfortably live. `force_refresh`
        skips the cache, which is what a harvester does when page three comes
        back 401 on a credential that was valid when the walk started — the
        pages already walked are kept and the page is retried, rather than a long
        harvest being reported stale against a good credential.

        The read, the acquisition and the write-back are one step under the
        store's claim. AAD rotates the refresh token on use, so a process that
        read the old one before another process rotated it would otherwise seal
        a token the provider has already retired. When the acquisition is
        refused, the store is re-read once: a value that changed underneath means
        somebody else rotated it, and the loser retries with the winner's token
        rather than reporting a credential that is not actually stale.
        """
        cached = self._access_token
        if not force_refresh and cached is not None and self._cached_token_is_live():
            return cached
        app = self._application()
        with self.store.exclusive():
            sealed = self._sealed()
            result = self._acquire(app, sealed, force_refresh=force_refresh)
            if "error" in result:
                rotated = self._sealed_or_none()
                if rotated is not None and rotated.refresh_token != sealed.refresh_token:
                    result = self._acquire(app, rotated, force_refresh=force_refresh)
                    sealed = rotated
                if "error" in result:
                    self._refuse(
                        result,
                        during="refreshing the Graph credential",
                        secret=sealed.refresh_token,
                    )
            self._assert_granted(result)
            self._rotate(sealed, result)
            return self._adopt(result)

    def check_health(self) -> Probe:
        """Whether this machine can obtain a Graph token right now.

        Reports; never raises. Four answers, because four different things are
        wrong and three of them are not "the credential is bad":

        - `ABSENT` — nothing enrolled. A fresh install, not a broken machine.
        - `FAILING`, stale — sign in again.
        - `FAILING`, interaction required — sign in again *interactively*,
          because a conditional-access challenge needs a human and a re-enrolment
          alone will not necessarily clear it.
        - `FAILING`, unreachable — wait, or look at the network. Telling an
          operator to re-issue a good token here sends them in a circle.

        `WARNING` on a healthy credential when this machine's clock is measurably
        adrift from the provider's: nothing is broken, and something will be.

        CAP-35's ten seconds are **not** enforced here, for the reason
        `ConnectorPort.check_health` states: a blocking call cannot cancel
        itself, so a bound this method merely promised would be a bound in name.
        This adapter is not a connector and is not in the registry that owns that
        bound — the Graph connector 33b builds is, and asking Graph for a token
        will happen inside its probe, under `ConnectorRegistry.check_health`'s
        deadline. Until then nothing calls this on a timer.
        """
        try:
            stored = self.store.read()
        except Exception as unreadable:  # noqa: BLE001 — a probe reports, never raises
            return Probe(
                self.instance,
                Health.FAILING,
                f"the sealed store holding {self.instance}'s credential could not "
                f"be read: {unreadable!r}",
                "That is custody, not auth — check the master key is enrolled "
                "and the keychain is unlocked. Whether the credential is good "
                "is unknown, which is not the same as bad.",
            )
        if stored is None:
            return Probe(
                self.instance,
                Health.ABSENT,
                f"no Microsoft credential is stored for {self.instance}",
                f"Enrol one with `pm-ai connector add graph {self.instance}` and "
                f"complete the sign-in it prints. Harvests are skipped until "
                f"then, which is a setup step outstanding rather than a fault.",
            )
        try:
            self.access_token()
        except CredentialStale as stale:
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance}'s stored credential is stale: {stale}",
                "Sign in again — the credential expired or was revoked. This is "
                "not a network problem and waiting will not clear it.",
            )
        except InteractionRequired as challenge:
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance} needs an interactive sign-in: {challenge}",
                "Conditional access wants a human — complete the challenge (MFA, "
                "a compliant device) by enrolling again. Neither waiting nor a "
                "silent refresh clears this one.",
            )
        except AuthDeclined as declined:
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance} does not hold every permission it declares: {declined}",
                "Grant the missing permission on the app registration, then "
                "enrol again. pm-ai refuses rather than running with part of the "
                "set, because a connector that works for some resources and 403s "
                "on others reports coverage it does not have.",
            )
        except GraphUnreachable as silent:
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance} could not reach the Microsoft token endpoint: {silent}",
                "Check this machine's network. The credential may be perfectly "
                "good — nothing was learned about it, which is not the same as "
                "it being rejected.",
            )
        except GraphAuthError as unmapped:
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance} could not obtain a token: {unmapped}",
                "Report this: pm-ai has no specific remedy for what the provider "
                "answered, which usually means an error code it has not seen.",
            )
        except Exception as broke:  # noqa: BLE001 — a probe reports, never raises
            # The catch-all is the contract, not a shrug. Everything above is a
            # refusal this adapter composed; anything else is a bug in it, and a
            # bug that escapes here takes the whole report down — one broken
            # connector hiding three healthy ones is the failure the
            # report-never-raise rule exists to prevent.
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance}'s auth adapter raised {broke!r} instead of "
                f"reporting. That is a bug in pm-ai, not a verdict about the "
                f"provider.",
                "Report this. Nothing on this machine needs changing on the "
                "strength of it.",
            )
        if self._skew is not None and abs(self._skew) > CLOCK_SKEW_TOLERANCE:
            return Probe(
                self.instance,
                Health.WARNING,
                f"{self.instance} holds a working credential, and this machine's "
                f"clock is {self._skew} from the provider's",
                "Fix the clock. Nothing is broken yet, and token lifetimes, "
                "meeting windows and coverage windows are all judged against it.",
            )
        return Probe(self.instance, Health.OK, f"{self.instance} obtained a token")

    # ── The provider, and everything that reads its answers ──────────────────

    @property
    def authority(self) -> str:
        """Where the sign-in happens. One string, so nothing derives a second."""
        return f"https://login.microsoftonline.com/{self.tenant}"

    def _application(self) -> Any:
        """The MSAL client, built once and kept for its account cache."""
        if self._app is None:
            self._app = self.client_factory(self.client_id, self.authority)
        return self._app

    def _resource_scopes(self) -> list[str]:
        """What is passed to MSAL: the declared set minus the reserved grants.

        Sorted, so two runs ask for the same thing in the same order and a
        recorded request is comparable. `offline_access` is removed because MSAL
        refuses it as user input and supplies it itself — see the module
        docstring, which records the measurement.
        """
        return sorted(self.scopes - {OFFLINE_ACCESS, "openid", "profile"})

    def _call(self, method: Callable[..., Any], *args: Any) -> dict[str, Any]:
        """Run one MSAL call, turning a transport failure into an unreachable.

        Broad on purpose. The only thing being contacted is the Microsoft token
        endpoint, and MSAL relays whatever its HTTP client raised — a DNS
        failure, a refused connection, a TLS error, a read timeout — as that
        library's own exception type, which this package may not import in order
        to name. So anything raised out of the call is a provider that did not
        answer. This adapter's own refusals pass through untouched, which is
        what keeps a declined consent from being reported as a dead network.
        """
        try:
            answered = method(*args)
        except GraphAuthError:
            raise
        except Exception as unreachable:  # noqa: BLE001 — see the docstring
            raise GraphUnreachable(
                f"the Microsoft token endpoint at {self.authority} did not "
                f"answer: {unreachable!r}. Nothing is known about the "
                f"credential, which is not the same as it being rejected."
            ) from unreachable
        return answered if isinstance(answered, dict) else {}

    def _prompt(self, flow: Mapping[str, Any]) -> str:
        """The sentence the human acts on.

        MSAL composes one already; it is used verbatim when present so the code
        and the URL cannot drift from what the flow actually issued. The
        fallback is composed from the same fields rather than from anything
        remembered.
        """
        message = flow.get("message")
        if isinstance(message, str) and message:
            return message
        return (
            f"To sign in, open {flow.get('verification_uri')} and enter the code "
            f"{flow.get('user_code')}. pm-ai is waiting and will not ask for your "
            f"password."
        )

    def _acquire(
        self, app: Any, sealed: SealedCredential, *, force_refresh: bool
    ) -> dict[str, Any]:
        """One token acquisition for the enrolled account.

        The cache is asked first when it holds that account, because a token
        still live in this process costs no round trip. It cannot be the only
        path: MSAL's cache is per-process and a freshly started daemon's is
        empty, which is precisely the case the sealed refresh token exists for.
        """
        account = self._cached_account(app, sealed)
        if account is not None:
            cached = self._call_silent(app, account, force_refresh=force_refresh)
            if cached:
                return cached
        return self._call(
            app.acquire_token_by_refresh_token, sealed.refresh_token, self._resource_scopes()
        )

    def _call_silent(
        self, app: Any, account: Mapping[str, Any], *, force_refresh: bool
    ) -> dict[str, Any]:
        """`acquire_token_silent`, whose `None` means "nothing cached", not a failure."""
        try:
            answered = app.acquire_token_silent(
                self._resource_scopes(), account=account, force_refresh=force_refresh
            )
        except GraphAuthError:
            raise
        except Exception as unreachable:  # noqa: BLE001 — see `_call`
            raise GraphUnreachable(
                f"the Microsoft token endpoint at {self.authority} did not "
                f"answer: {unreachable!r}."
            ) from unreachable
        return answered if isinstance(answered, dict) else {}

    def _cached_account(self, app: Any, sealed: SealedCredential) -> Mapping[str, Any] | None:
        """The enrolled account among MSAL's cached ones, identified explicitly.

        `None` when the cache is empty — a fresh process, not an ambiguity, and
        refusing there would refuse every daemon start. Ambiguity is refused:
        the cache holding accounts but not exactly one matching the enrolled
        `home_account_id` means the next call would refresh *some* identity, and
        which one would depend on iteration order.
        """
        accounts = app.get_accounts()
        if not accounts:
            return None
        matching = [
            account
            for account in accounts
            if account.get("home_account_id") == sealed.home_account_id
        ]
        if len(matching) != 1:
            raise CredentialStale(
                f"MSAL's cache holds {len(accounts)} account(s) and {len(matching)} "
                f"of them are the one {self.instance} was enrolled for, so the "
                f"account to refresh cannot be identified. Refusing rather than "
                f"picking one: the wrong identity would harvest somebody else's "
                f"calendar. Enrol {self.instance} again."
            )
        return matching[0]

    def _enrolling_account(self, app: Any, result: Mapping[str, Any]) -> str:
        """Which account just signed in, taken from the sign-in itself.

        The id token's claims are preferred because they describe *this* result;
        MSAL's account list is the fallback and is only unambiguous when it holds
        one account, which is why several are refused here too.
        """
        claims = result.get("id_token_claims")
        if isinstance(claims, Mapping):
            oid, tid = claims.get("oid"), claims.get("tid")
            if isinstance(oid, str) and oid and isinstance(tid, str) and tid:
                return f"{oid}.{tid}"
        accounts = [a for a in app.get_accounts() if a.get("home_account_id")]
        if len(accounts) == 1:
            return str(accounts[0]["home_account_id"])
        raise AuthDeclined(
            f"the sign-in succeeded and did not say which account it was for "
            f"({len(accounts)} in MSAL's cache, no usable id-token claims). "
            f"pm-ai will not seal a credential it cannot later match to an "
            f"account, because refreshing the wrong one harvests the wrong "
            f"calendar. Nothing was stored."
        )

    def _sealed(self) -> SealedCredential:
        """The stored credential, or the refusal that says to sign in."""
        stored = self._sealed_or_none()
        if stored is None:
            raise CredentialStale(
                f"no Microsoft credential is stored for {self.instance}, so there "
                f"is nothing to refresh. Enrol it and complete the sign-in."
            )
        return stored

    def _sealed_or_none(self) -> SealedCredential | None:
        raw = self.store.read()
        return None if raw is None else SealedCredential.decode(raw)

    def _rotate(self, used: SealedCredential, result: Mapping[str, Any]) -> None:
        """Seal the new refresh token when the provider issued one.

        AAD rotates on use and the old token stops working, so skipping this
        write means the *next* start reports stale on a credential that was
        renewed seconds ago. Written inside the caller's claim, never outside it.
        """
        issued = result.get("refresh_token")
        if isinstance(issued, str) and issued and issued != used.refresh_token:
            self.store.write(
                SealedCredential(
                    refresh_token=issued, home_account_id=used.home_account_id
                ).encode()
            )

    def _adopt(self, result: Mapping[str, Any]) -> str:
        """Take the access token, its expiry, and whatever the clock says.

        Expiry from the provider's relative `expires_in`, less `EXPIRY_MARGIN`.
        Skew from the id token's `iat` when there is one — a claim about the
        provider's clock at the moment it minted the token, which is the only
        comparison available without a second request.
        """
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise GraphAuthError(
                "the provider answered without an error and without an access "
                "token. pm-ai will not report a connector healthy on the "
                "strength of a response it cannot use."
            )
        self._access_token = token
        seconds = result.get("expires_in")
        moment = self.now()
        if isinstance(seconds, (int, float)) and seconds > 0:
            self._expires_at = moment + timedelta(seconds=float(seconds)) - EXPIRY_MARGIN
        else:
            # No lifetime declared: treat it as good for this call and no longer.
            # Caching a token of unknown age is how a request goes out with a
            # dead one and the 401 is blamed on the credential.
            self._expires_at = None
        claims = result.get("id_token_claims")
        if isinstance(claims, Mapping):
            issued = claims.get("iat")
            if isinstance(issued, (int, float)):
                self._skew = moment - datetime.fromtimestamp(float(issued), tz=timezone.utc)
        return token

    def _cached_token_is_live(self) -> bool:
        """Whether the token in hand is still safely usable."""
        return self._expires_at is not None and self.now() < self._expires_at

    def _assert_granted(self, result: Mapping[str, Any]) -> None:
        """Compare what was granted against what is declared, every single time.

        Every time, not only at enrolment: an administrator who revokes one
        permission afterwards leaves a connector that would fail at whichever
        resource happened to need it, weeks later, reported as a provider
        problem.

        A response that declares no scopes at all is refused too. It is not
        evidence of consent, and treating silence as agreement is how a
        connector ends up reporting coverage nobody granted.
        """
        declared = self.scopes - {OFFLINE_ACCESS, "openid", "profile"}
        raw = result.get("scope")
        granted = {
            # AAD echoes scopes as full URIs (`https://graph.microsoft.com/
            # Calendars.Read`) on some responses and bare on others; the
            # permission is the last segment either way.
            token.rsplit("/", 1)[-1].casefold()
            for token in (raw.split() if isinstance(raw, str) else [])
            if token
        }
        if not granted:
            raise AuthDeclined(
                f"the provider returned a token declaring no scopes, so pm-ai "
                f"cannot tell which of the {len(declared)} permissions it "
                f"declares were actually granted. Refusing: an unverified "
                f"consent is not a consent."
            )
        missing = sorted(scope for scope in declared if scope.casefold() not in granted)
        if missing:
            raise AuthDeclined(
                f"the Graph app registration did not grant {', '.join(missing)}, "
                f"which pm-ai declares and needs. It holds "
                f"{len(declared) - len(missing)} of {len(declared)} permissions, "
                f"and refuses to start on a partial set rather than working for "
                f"some resources and failing at others. Grant the missing "
                f"permission(s) and enrol again. Note especially: a missing "
                f"OnlineMeetings.Read reads later as a 403 that looks like a "
                f"tenant with transcripts switched off, which it is not."
            )

    def _refuse(
        self, result: Mapping[str, Any], *, during: str, secret: str | None = None
    ) -> NoReturn:
        """Turn one AAD error dict into the refusal whose remedy fits it.

        The `suberror` is read first. Conditional access arrives as
        `invalid_grant` with a suberror, and reading the code alone collapses
        the interactive remedy into "your token expired" — three remedies
        reported as two.

        `secret` is whatever credential this attempt used — the refresh token, or
        the device code during a sign-in. The description AAD wrote is checked
        against it before being interpolated, because "the token endpoint does
        not echo the credential" is an assumption about somebody else's string
        formatting and this refusal is where it would land if it were wrong.
        """
        code = str(result.get("error") or "").strip()
        suberror = str(result.get("suberror") or "").strip()
        description = _without(str(result.get("error_description") or "").strip(), secret)
        said = f"{code or 'an unnamed error'}{f' ({suberror})' if suberror else ''}"
        detail = f": {description}" if description else ""

        if suberror in _INTERACTION_SUBERRORS or code in _INTERACTION:
            raise InteractionRequired(
                f"Microsoft refused {during} and asked for an interactive "
                f"sign-in — {said}{detail}. This is conditional access, not an "
                f"expired credential: waiting will not clear it and neither will "
                f"a silent refresh."
            )
        if code in _DECLINED:
            raise AuthDeclined(
                f"consent was declined during {during} — {said}{detail}. Nothing "
                f"was stored."
            )
        if code in _ABANDONED:
            raise AuthTimedOut(
                f"the device code expired before anyone signed in with it "
                f"({said}{detail}). Nothing was stored — start the sign-in again "
                f"and complete it while the code is live."
            )
        if code in _STALE:
            raise CredentialStale(
                f"Microsoft rejected the stored credential during {during} — "
                f"{said}{detail}. Sign in again; this is the credential, not the "
                f"network."
            )
        raise GraphAuthError(
            f"Microsoft refused {during} with {said}{detail}, which pm-ai has no "
            f"specific remedy for. Refusing rather than guessing at one of the "
            f"states it does understand."
        )
