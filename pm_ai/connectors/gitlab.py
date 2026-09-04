"""GitLab harvester — class H egress, read-only by construction (AD-1, AD-9).

The HTTP call is stubbed for the slice; everything around it is the real shape a
connector must have: one method, no scheduling, no id minting, no writes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pm_ai.domain.events import CommitPayload, NormalizedEvent, ObservedEventType, Provenance
from pm_ai.domain.harvest import Cursor, HarvestResult
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import DataScope, SourceRef, resolve_actor
from pm_ai.domain.lifecycle import CoverageWindow

# The row `sample_events` maps. Fixed values, because the point of a sample is
# that two runs produce the same shape — and it goes through the *same* mapping
# `harvest` uses, so a sample cannot drift from what the connector really emits.
SAMPLE_ROW = {
    "sha": "0" * 40,
    "author_email": "sample@example.invalid",
    "message": "a sample commit, mapped by the same code path as a real one",
    # A real provider clock, not `None`. The sample is the one event the AD-27
    # and AD-34 gates inspect, and with no `occurred_at` the AD-35 path — a
    # provider timestamp, and its plausibility — was the one thing the fixture
    # they read could not represent. Fixed and in the past, so it stays
    # deterministic and stays plausible: after `EARLIEST_PLAUSIBLE`, and never
    # inside the five-minute future skew tolerance.
    "committed_at": datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc),
}


def _stubbed_reach() -> str:
    """The placeholder transport, named so a verdict can recognise it.

    A lambda could not be compared by identity, which is what `check_health`
    needs to tell "GitLab answered" apart from "nothing was asked".
    """
    return "gitlab (stubbed transport) answered"


@dataclass
class GitLabConnectorAdapter:
    project: str
    scope: DataScope
    name: str = "gitlab"
    system: str = "gitlab"
    # Injected (AD-30), never read from the ambient environment: AD-35's coverage
    # windows are a fail-closed guard, and a guard you cannot test deterministically
    # is a guard you cannot trust.
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    _fake_api: list[dict] = field(default_factory=list)
    # Whether a token has been enrolled for this instance. `None` is the
    # first-run state `check_health` reports as ABSENT — a machine nobody has
    # finished setting up, which is not a broken one. Story `8b` owns putting a
    # real credential here; until then it is injected like everything else.
    credential: str | None = None
    # Injected exactly as `_fake_api` is, and for the same reason: the HTTP call
    # is stubbed for this slice, and a health probe that reached the network
    # could not be exercised against the failures it exists to report — a
    # provider that refuses, and one that never answers at all.
    reach: Callable[[], str] = _stubbed_reach

    def emits(self) -> frozenset[ObservedEventType]:
        """Only from the core taxonomy — a connector may not mint a type (AD-27)."""
        return frozenset({ObservedEventType.COMMIT_PUSHED})

    @property
    def instance(self) -> str:
        """This connector's registry identity: the system, plus what it covers.

        `name` is `"gitlab"` on every instance, so it cannot tell two projects
        apart. The same string `CoverageWindow.connector_instance` already
        carries, spelled once so the registry key, the coverage window and the
        probe row cannot drift.
        """
        return f"gitlab:{self.project}"

    def _to_event(self, row: dict) -> NormalizedEvent:
        """One provider row, mapped. The only place this connector builds an event.

        `sample_events` goes through here too, so the sample an architecture gate
        inspects is built by the code a real harvest runs rather than beside it.
        """
        return NormalizedEvent(
            scope=self.scope,
            type=ObservedEventType.COMMIT_PUSHED,
            # AD-34 grammar — not a URL, so it joins across connectors
            source_ref=SourceRef.parse(f"gitlab:{self.project}:commit:{row['sha']}"),
            # AD-34 — a native handle resolves to an Actor or to UNRESOLVED,
            # never to itself
            actor=resolve_actor(system="gitlab", handle=row["author_email"]),
            occurred_at=row["committed_at"],  # provider clock (AD-35)
            payload=CommitPayload(sha=row["sha"], message=row["message"]),
            # AD-36 — a connector may NEVER assert `external`. It cannot see
            # the executed-mutation ledger, so it cannot know whether this is
            # pm-ai's own write coming back. Normalization decides; this
            # emits the fail-closed default. Hard-coding EXTERNAL here made
            # our own comments admissible as evidence that our own promises
            # were kept.
            authored_by=Provenance.UNKNOWN,
            # no `id`: the storage service mints the surrogate (AD-34)
        )

    def sample_events(self) -> tuple[NormalizedEvent, ...]:
        """One representative event, built offline through the harvest mapping.

        Non-empty by construction. The AD-34 gate asserts over every event a
        connector samples, so an empty tuple would pass it without executing a
        single assertion — the vacuous shape that gate spent its whole life in
        while the module it imports did not exist.
        """
        return (self._to_event(dict(SAMPLE_ROW)),)

    def check_health(self) -> Probe:
        """Whether this instance has a credential, and whether GitLab answers.

        Reports; never raises. `ABSENT` before a token is enrolled, because a
        fresh install is not a broken machine; `FAILING` when the provider
        refused or could not be reached, because a dead connector reads forever
        as "no coverage yet" (AD-39) unless something says otherwise.

        The ten-second bound is not enforced here. This call cannot cancel
        itself, so `ConnectorRegistry.check_health` bounds the *wait* and
        abandons the attempt — see its docstring.
        """
        # `None`, empty and whitespace are one state: no usable credential.
        # A blank string is what a half-finished enrolment leaves behind, and
        # reporting it as configured would say setup is done when it is not.
        if not (self.credential or "").strip():
            return Probe(
                self.instance,
                Health.ABSENT,
                f"no usable credential is stored for {self.instance}",
                "No credential can be enrolled on this build yet: `pm-ai "
                "connector add gitlab` is story 8b and is not implemented. "
                "Harvests are skipped until then, which is a setup step "
                "outstanding rather than a fault.",
            )
        try:
            answer = self.reach()
        except Exception as unreachable:  # noqa: BLE001 — a probe reports, never raises
            return Probe(
                self.instance,
                Health.FAILING,
                f"{self.instance} did not answer: {unreachable!r}",
                "Check the token has not expired and that this machine can "
                "reach the GitLab host. An unreachable connector is "
                "indistinguishable from a sleeping laptop in the coverage "
                "windows (AD-35), so it has to be reported here.",
            )
        if self.reach is _stubbed_reach:
            # `OK` is a claim that this machine reached GitLab. The default
            # transport is a stub that returns a string without opening a
            # socket, so reporting `OK` from it would put a reachability
            # verdict in the report that nothing measured. `8b` wires the real
            # transport; until then the honest answer is that it is untested.
            return Probe(
                self.instance,
                Health.WARNING,
                f"{self.instance} has a credential, but its transport is still "
                f"a stub — reachability has not been tested",
                "Nothing to fix on this machine. The GitLab connector gains a "
                "real HTTP transport in story 8b; until then this row reports "
                "what is configured, not what answered.",
            )
        return Probe(self.instance, Health.OK, f"{answer}")

    def harvest(self, since: Cursor) -> HarvestResult:
        started = self.now()
        offset = int(since.token or b"0")
        rows = self._fake_api[offset:]

        events = tuple(self._to_event(r) for r in rows)
        return HarvestResult(
            events=events,
            cursor=Cursor(str(len(self._fake_api)).encode()),
            # AD-35 — reported in the return type so it cannot be forgotten
            coverage=CoverageWindow(
                connector_instance=self.instance,
                start=started - timedelta(hours=4),
                end=started,
            ),
        )
