"""Which connectors this process holds, and whether each can reach its provider.

## Why a registry exists beside `Daemon.connectors`

`pm_ai.app.wiring.build()` already puts every connector it constructs into a
`Daemon.connectors` dict. Nothing outside the composition root can reach that
dict, so no architecture check could ask the question both of the AD-27 and
AD-34 gates are written to ask — "for every connector, ...". Those two gates
import this module, and skipped from the day they were written until it existed.

The division is deliberate and narrow: **`Daemon.connectors` holds the
instances; this enumerates them.** Nothing here constructs a connector, and
nothing here schedules one. `all_connectors()` takes no arguments because the
gates call it that way, and `GitLabConnectorAdapter` needs a project and a scope
that only `build()` knows — so the registry is *populated at composition*, not at
import. A module-level registry that constructed its own connectors would need a
hardcoded project literal, which is the thing AD-11 exists to keep out.

The consequence is stated rather than hidden: **before composition the registry
is empty.** That is not an error — it is a first-run state, and `pm-ai connector
check` prints it as one. It is also exactly why the gates assert the registry is
non-empty *before* their loops: a `for` over nothing passes every assertion in
its body without running one, and a gate that turns from skipped to green while
proving nothing is worse than the skip, which `-rs` at least shows.

## The load path

`ConnectorRegistry` is an ordinary object and `install()` makes one the process
default. First-party and local today; `8b`'s enrolment and any later signature
verification attach by building a registry differently and installing it, with
no caller here changing. `install()` *replaces* rather than merges, so a second
`build()` in one process — every test that wires a daemon — describes that
daemon rather than accumulating the last three.

## The bound on `check_health`

CAP-35 requires an answer within ten seconds. A connector's probe reports and
never raises, but it cannot promise to *return*: a blocking socket read is not
cancellable from outside, and a bound the adapter is merely asked to honour is
not a bound. So the bound here is on **waiting**. Every probe is started at once,
the collection stops at the deadline, and a probe still running at that point is
reported `FAILING` and abandoned — its thread is left to finish or not, and its
answer is discarded whenever it arrives.

This is the one place in `pm_ai.connectors` that starts a thread, and it is not
the thing AD-9 forbids. AD-9 keeps *cadence* out of connectors — no connector
polls, retries, or schedules itself, because per-connector schedulers compete for
rate limits and drift out of the daemon's cursor accounting. A probe deadline
owns no cadence: it runs once, when something invokes it, and holds no state
between calls. `tests/architecture/test_static_rules.py` names this file as the
single exemption so the rule keeps covering every other connector.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

from pm_ai.domain.events import NormalizedEvent
from pm_ai.domain.health import Health, Probe, Report
from pm_ai.ports import ConnectorPort

__all__ = [
    "ConnectorRegistry",
    "DuplicateConnector",
    "HEALTH_PROBE_SECONDS",
    "all_connectors",
    "check_health",
    "default_registry",
    "install",
    "sample_events",
]

# CAP-35's bound, in seconds. Named rather than defaulted inline so `pm-ai
# connector check` and this module cannot each carry their own idea of it.
HEALTH_PROBE_SECONDS = 10.0


class DuplicateConnector(Exception):
    """Two connectors registered under one instance name, so one would be lost.

    Refused at registration rather than resolved by last-write-wins. The instance
    name is what a cursor, a coverage window and a stored credential are all keyed
    by, so two connectors sharing one would interleave their cursors — each
    re-harvesting from where the other left off — and the symptom would be missing
    events, weeks later, with nothing in the logs.

    Not a `KeyError` or a `ValueError`: a caller catching either of those is
    catching something else, and this is a composition-time refusal that should
    stop a daemon starting rather than be absorbed by a broad `except`.
    """


class ConnectorRegistry:
    """The connectors one composition holds, in registration order.

    Order is preserved because a health report is read by a human and a set's
    iteration order would reshuffle the rows between runs.
    """

    def __init__(self) -> None:
        self._connectors: dict[str, ConnectorPort] = {}

    def register(self, connector: ConnectorPort, *, instance: str | None = None) -> None:
        """Add `connector` under `instance`, defaulting to its own `name`.

        `instance` is separate from `name` because `name` is the *system*
        (`"gitlab"` on every GitLab adapter) while what must be unique is the
        instance — `gitlab:alpha` and `gitlab:beta` are two connectors of one
        kind. Defaulting to `name` keeps the common single-instance case quiet.

        Raises `DuplicateConnector` rather than replacing.
        """
        key = instance if instance is not None else connector.name
        if key in self._connectors:
            raise DuplicateConnector(
                f"a connector is already registered as {key!r}. Two connectors "
                f"under one instance name share a cursor and a credential, so "
                f"one of them silently re-harvests from the other's position."
            )
        self._connectors[key] = connector

    def all_connectors(self) -> tuple[ConnectorPort, ...]:
        """Every registered connector. Empty before composition, and that is a state."""
        return tuple(self._connectors.values())

    def instances(self) -> tuple[str, ...]:
        """The names registered, for a diagnostic that reports membership only.

        `doctor` lists these without contacting anything: membership is a fact
        about this process, health is a fact about a provider, and mixing them
        puts a network call in the command an operator runs when the network is
        the thing that is broken.
        """
        return tuple(self._connectors)

    def sample_events(self) -> tuple[NormalizedEvent, ...]:
        """Every registered connector's samples, flattened.

        The convenience over `for c in all_connectors(): c.sample_events()`, for
        checks that care about the events rather than about which connector
        produced them. Empty when nothing is registered — so a caller asserting
        over it must assert it is non-empty first, exactly as the gates do.
        """
        return tuple(e for c in self._connectors.values() for e in c.sample_events())

    def check_health(self, *, timeout: float = HEALTH_PROBE_SECONDS) -> Report:
        """Every connector probed at once, with one deadline over the lot.

        Returns a `Report` in registration order, one `Probe` per connector, and
        raises nothing whatever the adapters do:

        - a probe that returns is reported as it answered;
        - a probe that **raises** is reported `FAILING` for that connector alone,
          because one broken adapter hiding three healthy ones is the whole
          reason the report-never-raise rule exists;
        - a probe still running at `timeout` is reported `FAILING` and
          **abandoned**. Its thread is not cancelled — nothing can cancel a
          blocking read — so it may still be running when this returns, and
          whatever it eventually answers is discarded.

        The deadline is over the whole call, not per connector: probes run
        concurrently, so ten silent connectors cost ten seconds rather than a
        hundred, and CAP-35's bound holds for the command a human is waiting on.
        """
        connectors = tuple(self._connectors.items())
        if not connectors:
            return Report(())
        deadline = time.monotonic() + timeout
        # Daemon threads, not a pool. A probe past the deadline is *abandoned*,
        # and `ThreadPoolExecutor` cannot abandon: its workers are non-daemon and
        # joined by `threading._register_atexit`, so `shutdown(wait=False)`
        # returns on time and the interpreter then blocks on the same worker at
        # exit. Measured 2026-09-04 before this change: `check_health` returned
        # in 0.31s against a 0.3s bound while the process took 8.23s to die, so
        # the bound held for the report and not for the command a human waits on
        # — which is the only thing CAP-35 is about.
        results: dict[str, Probe] = {}

        def _ask(instance: str, connector: ConnectorPort) -> None:
            try:
                results[instance] = connector.check_health()
            except BaseException as raised:  # noqa: BLE001 - a probe never raises
                # `check_health` is documented never to raise. A connector that
                # breaks that contract must not take the other connectors'
                # report down with it, so the breach is reported as its own row.
                results[instance] = Probe(
                    instance,
                    Health.FAILING,
                    f"{instance}'s health probe raised {raised!r} instead of "
                    f"reporting. That is a bug in the connector, not a verdict "
                    f"about the provider.",
                    "Report this: a probe is required to return a Probe.",
                )

        threads = []
        for instance, connector in connectors:
            thread = threading.Thread(
                target=_ask,
                args=(instance, connector),
                name=f"connector-probe-{instance}",
                daemon=True,
            )
            thread.start()
            threads.append((instance, thread))

        probes = []
        for instance, thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
            probes.append(
                results.get(instance)
                or Probe(
                    instance,
                    Health.FAILING,
                    f"{instance} did not answer within {timeout:g}s and was "
                    f"abandoned. Whether it is reachable is unknown, which is "
                    f"not the same as unreachable.",
                    "Check the provider's status and this connector's "
                    "credential; a probe this slow usually means neither is "
                    "answering.",
                )
            )
        return Report(tuple(probes))


# ── The process default, installed by the composition root ───────────────────

_DEFAULT = ConnectorRegistry()


def install(connectors: Iterable[ConnectorPort] | ConnectorRegistry) -> ConnectorRegistry:
    """Make `connectors` the registry this process enumerates, and return it.

    Called by `pm_ai.app.wiring.build()` with the connectors it just constructed.
    Replaces rather than merges: a second `build()` describes the daemon it just
    built, not that one plus every earlier one — which matters because the test
    suite wires dozens of daemons in one process and a merging registry would
    report them all, then refuse on the first repeated instance name.

    Accepts a built `ConnectorRegistry` too, which is how an alternative load
    path — `8b`'s enrolment, a signature-verifying loader — attaches without
    anything here knowing about it.
    """
    global _DEFAULT
    if isinstance(connectors, ConnectorRegistry):
        _DEFAULT = connectors
        return _DEFAULT
    fresh = ConnectorRegistry()
    for connector in connectors:
        fresh.register(connector)
    _DEFAULT = fresh
    return _DEFAULT


def default_registry() -> ConnectorRegistry:
    """The installed registry. Empty until something composes one."""
    return _DEFAULT


def all_connectors() -> tuple[ConnectorPort, ...]:
    """Every connector this process holds — the accessor the AD gates call.

    No arguments, because those gates call it that way and because the answer is
    a property of the composition rather than of any caller.
    """
    return _DEFAULT.all_connectors()


def sample_events() -> tuple[NormalizedEvent, ...]:
    """Every registered connector's sample events, flattened."""
    return _DEFAULT.sample_events()


def check_health(*, timeout: float = HEALTH_PROBE_SECONDS) -> Report:
    """Probe every registered connector, within `timeout` seconds in total."""
    return _DEFAULT.check_health(timeout=timeout)
