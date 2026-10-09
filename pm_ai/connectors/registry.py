"""Enumerating one composition's connectors, and asking whether each can reach its provider.

## What this is, and what it is not

`pm_ai.app.wiring.build()` puts every connector it constructs into
`Daemon.connectors`, and that dict is **the one inventory**: a harvest reads it,
and so does `pm-ai connector check`. `ConnectorRegistry` is an ordinary object
built over such an inventory when something needs to enumerate or probe it —
nothing here constructs a connector, schedules one, or keeps one.

There is deliberately **no process-wide registry** (story 8k, AD-30). Until 8k
the composition root copied its connectors into a module global and `connector
check` read the copy, and the two disagreed once: `connector check` listed an
instance `run_harvest` could not find. A global also meant a second composition
in one process silently replaced the first one's list, and every test that
wired a daemon left its list behind for whatever ran next. Whoever needs the
connectors is handed the daemon that holds them.

**Where another load path attaches.** The removed `install()` was documented as
the hook for `8b`'s enrolment and any later signature-verifying loader. Both now
attach inside `build()`: `wiring._enrolled_connectors` turns what `pm-ai
connector add` wrote into adapters, and `build()` puts them into
`Daemon.connectors` beside the built-ins. A verifying loader belongs at the same
place — it decides what enters that dict, and nothing here changes.

An empty registry is a state, not an error. `pm-ai connector check` prints it as
a first run, and the architecture gates that loop over a daemon's connectors
assert the inventory is non-empty *before* their loops: a `for` over nothing
passes every assertion in its body without running one.

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
from collections.abc import Callable
from typing import TypeVar

from pm_ai.domain.events import NormalizedEvent
from pm_ai.domain.health import Health, Probe, Report
from pm_ai.ports import ConnectorPort, DuplicateConnector

__all__ = [
    "ConnectorRegistry",
    "DuplicateConnector",
    "HEALTH_PROBE_SECONDS",
    "run_bounded",
]

# CAP-35's bound, in seconds. Named rather than defaulted inline so `pm-ai
# connector check` and this module cannot each carry their own idea of it.
_T = TypeVar("_T")

HEALTH_PROBE_SECONDS = 10.0


# `DuplicateConnector` was declared here until story `8b`, and now lives in
# `pm_ai.ports` — re-exported above so this module's name for it is unchanged.
# The move was forced rather than tidy: `8b`'s enrolment raises the same refusal
# from `pm_ai.core`, which sits below this package in the enforced layer stack
# and may not import it. Two classes with one name, in two packages that cannot
# see each other, is a refusal a caller catches half of.



def run_bounded(call: Callable[[], _T], *, timeout: float, label: str) -> _T:
    """Run `call` on a daemon thread and give up on it at `timeout`.

    The one bounded call in this codebase, and it lives here because
    `connectors/registry.py` is the single file AD-9's no-scheduling rule
    exempts — a deadline is not a cadence. Story 8b's credential probe needs the
    same bound and `pm_ai/connectors/probe.py` calls this rather than starting
    its own thread, so the exemption stays one file wide instead of two.

    A blocking socket read cannot be cancelled from outside, so the thread is
    *abandoned* rather than killed: it may still be running when this returns,
    and whatever it eventually answers is discarded. Daemon, so an abandoned
    probe cannot hold the interpreter open at exit — which it did until
    2026-09-04, when `check_health` answered a 0.3s bound in 0.31s and the
    process took 8.23s to die.

    Raises `TimeoutError` at the deadline. Anything the call itself raises is
    re-raised here, so a caller's own `except` still sees what it expects.
    """
    box: dict[str, object] = {}

    def _run() -> None:
        try:
            box["value"] = call()
        except BaseException as raised:  # noqa: BLE001 - relayed, not swallowed
            box["raised"] = raised

    thread = threading.Thread(target=_run, name=f"bounded-{label}", daemon=True)
    thread.start()
    thread.join(timeout)
    if "raised" in box:
        raise box["raised"]  # type: ignore[misc]
    if "value" not in box:
        raise TimeoutError(
            f"{label} did not answer within {timeout:g}s and was abandoned. "
            f"Whether it would have answered is unknown, which is not the same "
            f"as a refusal."
        )
    return box["value"]  # type: ignore[return-value]


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
        kind. Omitted, it is taken from the connector's own `instance` when it
        has one, and only then from `name`.

        Raises `DuplicateConnector` rather than replacing.
        """
        # An adapter that already knows its own instance name is asked for it
        # before falling back to the system name. Defaulting to `name` meant two
        # GitLab projects registered without an explicit `instance` collided on
        # `"gitlab"` and aborted, while `build()` escaped it only because it
        # passed `instance` explicitly.
        key = instance or getattr(connector, "instance", None) or connector.name
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
        """Every connector's samples in this registry, flattened.

        The convenience over calling `sample_events()` on each of
        `self.all_connectors()`, for a check that cares about the events rather
        than about which connector produced them. Nothing in production calls
        it; the AD-27 and AD-34 gates loop over a daemon's connectors one by one,
        so they can name the connector that failed. Empty when nothing is
        registered — so a caller asserting over it must assert it is non-empty
        first, exactly as the gates do.
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
