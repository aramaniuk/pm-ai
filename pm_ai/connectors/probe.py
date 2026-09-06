"""The live credential check story 8b runs before it stores anything (CAP-35).

Here rather than in `pm_ai.core` because asking a provider whether it accepts a
credential is I/O, and `core-is-io-free` forbids every HTTP client in that
package. `pm_ai.core.connector_enrolment` receives this as a parameter typed
`CredentialProbePort`, which is the same arrangement that makes story 8d's
health probes legal in this package and illegal one layer down.

**No transport yet, for either system.** Story 33a brought the Graph
device-code flow and the first real HTTP client with it, and deliberately no
probe: that slice fetches no resource and enrols no Graph connector, so a
`graph` entry here would be a check nothing calls. GitLab's transport is still a
stub. A system pm-ai has no probe for is refused by name —
`UnknownConnectorSystem` — rather than passed, because a probe that returned
"fine" without asking anything would put a credential on disk on the strength of
a check that never happened. That is the failure this whole slice is ordered to
prevent, and it would be introduced by the probe itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from pm_ai.connectors.registry import run_bounded
from pm_ai.ports import (
    ProbeFailed,
    ProbeUnreachable,
    UnknownConnectorSystem,
)

#: CAP-35's ten seconds, over the credential probe as well as the health one.
#: Named separately from `HEALTH_PROBE_SECONDS` because they bound different
#: things — one command a human is waiting on, one report over every connector —
#: and a later decision to change one should not silently move the other.
CREDENTIAL_PROBE_SECONDS = 10.0

__all__ = ["CREDENTIAL_PROBE_SECONDS", "PROBES", "probe_credential"]


def _gitlab(credential: str) -> str:
    """GitLab's answer, once there is a transport to hear it.

    `GitLabConnectorAdapter.reach` is still a stub that opens no socket, so
    there is nothing here that could honestly answer. Refusing is the only
    option that does not lie: accepting would seal a credential nobody checked.
    """
    raise ProbeFailed(
        "pm-ai cannot yet check a GitLab credential: the GitLab connector's "
        "transport is still a stub that opens no socket, so there is nothing "
        "to ask. Enrolment refuses rather than sealing a credential on the "
        "strength of a check that did not happen. GitLab's real transport is "
        "still outstanding; story 33a built Microsoft Graph's, not this one's."
    )


#: The systems pm-ai can ask about, by name. A system absent from this mapping
#: is refused rather than assumed good.
PROBES: Mapping[str, Callable[[str], str]] = {
    "gitlab": _gitlab,
}


def probe_credential(system: str, credential: str) -> str:
    """`CredentialProbePort` — ask `system` whether it accepts `credential`.

    Returns a short sentence naming what answered. Never returns the credential
    or anything derived from it, and never returns at all for a system pm-ai
    has no probe for.
    """
    try:
        ask = PROBES[system]
    except KeyError:
        known = ", ".join(sorted(PROBES)) or "none"
        raise UnknownConnectorSystem(
            f"pm-ai has no credential probe for {system!r}, so it cannot check "
            f"a credential for one. Enrolment refuses rather than sealing an "
            f"unchecked secret. Known systems: {known}."
        ) from None
    try:
        return run_bounded(
            lambda: ask(credential),
            timeout=CREDENTIAL_PROBE_SECONDS,
            label=f"{system} credential probe",
        )
    except TimeoutError as silent:
        # CAP-35's bound, enforced rather than merely documented.
        # `ProbeUnreachable` is the distinct refusal an operator needs: a
        # provider that never answered is not a credential that was rejected,
        # and reissuing a good token is the wrong repair. Unbounded, this hung
        # `pm-ai connector add` indefinitely while holding the sealed store's
        # exclusive claim open — so one silent provider wedged every later
        # enrolment too.
        raise ProbeUnreachable(
            f"{system} did not answer within {CREDENTIAL_PROBE_SECONDS:g}s, so "
            f"the credential could not be checked and nothing was stored."
        ) from silent
