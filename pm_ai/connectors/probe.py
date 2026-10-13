"""The live credential check story 8b runs before it stores anything (CAP-35).

Here rather than in `pm_ai.core` because asking a provider whether it accepts a
credential is I/O, and `core-is-io-free` forbids every HTTP client in that
package. `pm_ai.core.connector_enrolment` receives this as a parameter typed
`CredentialProbePort`, which is the same arrangement that makes story 8d's
health probes legal in this package and illegal one layer down.

**No entries, and none is owed.** Neither system pm-ai builds types its
credential into this table. `pm-ai connector add graph` signs in and runs the
connector's own bounded health check behind the composition root
(`pm_ai.app.wiring.GraphEnrolment`, story 8l); `pm-ai connector add gitlab`
asks for the host and the project path, takes the token at a hidden prompt and
runs the GitLab transport's own health check — one commit of that project —
behind the composition root too (`pm_ai.app.wiring.GitLabEnrolment`, story 8o).
Both bound their check at this module's `CREDENTIAL_PROBE_SECONDS`.

So `PROBES` is empty, and `pm-ai connector add <any other system>` meets it
and is refused by name — `UnknownConnectorSystem` — rather than passed, because
a probe that returned "fine" without asking anything would put a credential on
disk on the strength of a check that never happened. That is the failure this
whole slice is ordered to prevent, and it would be introduced by the probe
itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from pm_ai.connectors.registry import run_bounded
from pm_ai.ports import ProbeUnreachable, UnknownConnectorSystem

#: CAP-35's ten seconds, over the credential probe as well as the health one.
#: Named separately from `HEALTH_PROBE_SECONDS` because they bound different
#: things — one command a human is waiting on, one report over every connector —
#: and a later decision to change one should not silently move the other.
CREDENTIAL_PROBE_SECONDS = 10.0

__all__ = ["CREDENTIAL_PROBE_SECONDS", "PROBES", "probe_credential"]


#: The systems pm-ai can ask about, by name. A system absent from this mapping
#: is refused rather than assumed good.
PROBES: Mapping[str, Callable[[str], str]] = {}


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
        # `pm-ai connector add` indefinitely. It holds no claim while it waits:
        # enrolment probes before it takes the sealed store's exclusive claim,
        # so a silent provider stalls this one command and no other enrolment.
        raise ProbeUnreachable(
            f"{system} did not answer within {CREDENTIAL_PROBE_SECONDS:g}s, so "
            f"the credential could not be checked and nothing was stored."
        ) from silent
