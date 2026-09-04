"""The connector registry, and every row of story 8d's matrix.

Two properties carry most of the weight here.

**An empty registry is a state, not an error** — and the checks that read the
registry must be able to tell the difference. That is why `test_empty_registry_*`
exists in two halves: one asserting the registry says so quietly, the other
asserting the architecture gates *fail* over it rather than passing over nothing.

**A probe reports; it never raises** — including when the adapter behind it does,
and including when it never returns at all. Both are simulated, because neither
can be produced by asking a real provider nicely.
"""

from __future__ import annotations

import threading
import time

import pytest

from pm_ai.connectors import registry as registry_module
from pm_ai.connectors.gitlab import GitLabConnectorAdapter
from pm_ai.connectors.registry import ConnectorRegistry, DuplicateConnector
from pm_ai.domain.events import ObservedEventType
from pm_ai.domain.health import Health, Probe
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.ports import ConnectorPort

SCOPE = DataScope(ScopeKind.PROJECT, "alpha")


def gitlab(project: str = "alpha", **kwargs) -> GitLabConnectorAdapter:
    return GitLabConnectorAdapter(project=project, scope=SCOPE, **kwargs)


class FakeConnector:
    """A whole `ConnectorPort` whose probe does whatever a row needs.

    Every member the port declares, not just the one under test. A partial fake
    satisfies a parameter that is implicitly `Any` and proves nothing — the
    lesson `keychain_reachable`'s fake taught on 2026-08-25.
    """

    system = "fake"

    def __init__(self, name: str, probe=None):
        self.name = name
        self._probe = probe or (lambda: Probe(name, Health.OK, "answered"))

    def emits(self):
        return frozenset({ObservedEventType.COMMIT_PUSHED})

    def harvest(self, since):  # pragma: no cover - never called here
        raise AssertionError("the registry must not harvest")

    def sample_events(self):
        return gitlab().sample_events()

    def check_health(self) -> Probe:
        return self._probe()


@pytest.fixture(autouse=True)
def _isolated_default():
    """Restore the process default registry around every test.

    The default is process-wide by design — `all_connectors()` takes no
    arguments — so a test that installs one would otherwise decide what the next
    test sees.
    """
    before = registry_module.default_registry()
    yield
    registry_module.install(before)


# ── Enumeration ──────────────────────────────────────────────────────────────


def test_two_registered_connectors_are_both_returned():
    reg = ConnectorRegistry()
    reg.register(gitlab("alpha"), instance="gitlab:alpha")
    reg.register(gitlab("beta"), instance="gitlab:beta")

    assert len(reg.all_connectors()) == 2
    assert reg.instances() == ("gitlab:alpha", "gitlab:beta")


def test_registration_order_is_preserved():
    """A health report is read by a human; reshuffled rows between runs are noise."""
    reg = ConnectorRegistry()
    for n in ("c", "a", "b"):
        reg.register(FakeConnector(n))
    assert reg.instances() == ("c", "a", "b")


def test_an_empty_registry_is_empty_and_says_so():
    """The matrix's "enumerated before composition" row. Not an error."""
    reg = ConnectorRegistry()
    assert reg.all_connectors() == ()
    assert reg.instances() == ()
    assert reg.sample_events() == ()
    assert reg.check_health().probes == ()


def test_an_empty_registry_would_make_the_architecture_gates_fail():
    """The row that matters, asserted as the gates assert it.

    The defect 8d exists to prevent is in the *verification*, not in the code:
    both AD gates assert only inside a `for` body, so an empty registry passes
    them without running one assertion. This is that shape, proven to fail.
    """
    registry_module.install(ConnectorRegistry())
    assert registry_module.all_connectors() == ()
    with pytest.raises(AssertionError):
        connectors = registry_module.all_connectors()
        assert connectors, "an empty registry must fail the gate, not satisfy it"


def test_a_duplicate_instance_name_is_refused_at_registration():
    reg = ConnectorRegistry()
    reg.register(gitlab("alpha"), instance="gitlab:alpha")
    with pytest.raises(DuplicateConnector) as refusal:
        reg.register(gitlab("alpha"), instance="gitlab:alpha")
    assert "gitlab:alpha" in str(refusal.value)
    assert len(reg.all_connectors()) == 1, "the refused registration must not land"


def test_one_kind_of_connector_may_be_registered_twice_under_two_instances():
    """`name` is the system; the instance is what must be unique."""
    reg = ConnectorRegistry()
    reg.register(gitlab("alpha"), instance="gitlab:alpha")
    reg.register(gitlab("beta"), instance="gitlab:beta")
    assert {c.name for c in reg.all_connectors()} == {"gitlab"}


def test_instance_defaults_to_the_connector_name():
    reg = ConnectorRegistry()
    reg.register(gitlab("alpha"))
    assert reg.instances() == ("gitlab",)


# ── Samples ──────────────────────────────────────────────────────────────────


def test_every_connector_samples_at_least_one_event_with_no_id():
    reg = ConnectorRegistry()
    reg.register(gitlab("alpha"), instance="gitlab:alpha")
    events = reg.sample_events()
    assert events
    for event in events:
        assert getattr(event, "id", None) is None


def test_samples_come_from_the_harvest_mapping():
    """A hand-written sample would drift; the gate would then check a decoration."""
    connector = gitlab("alpha")
    sample = connector.sample_events()[0]
    assert sample.type in connector.emits()
    assert sample.scope == SCOPE
    assert sample.source_ref.system == "gitlab"


def test_a_gitlab_adapter_satisfies_the_connector_port():
    assert isinstance(gitlab(), ConnectorPort)


# ── Health ───────────────────────────────────────────────────────────────────


def test_a_reachable_provider_is_healthy_and_fast():
    reg = ConnectorRegistry()
    reg.register(gitlab("alpha", credential="t"), instance="gitlab:alpha")
    started = time.monotonic()
    report = reg.check_health()
    assert report.healthy
    assert [p.health for p in report.probes] == [Health.OK]
    assert time.monotonic() - started < 10.0


def test_no_credential_is_absent_not_failing():
    """A fresh install is not a broken machine."""
    reg = ConnectorRegistry()
    reg.register(gitlab("alpha"), instance="gitlab:alpha")  # credential=None
    (probe,) = reg.check_health().probes
    assert probe.health is Health.ABSENT
    assert probe.health is not Health.FAILING
    assert probe.remediation, "an ABSENT probe must say how to finish setup"


def test_a_refusing_provider_is_failing_and_distinct_from_absent():
    def refuse() -> str:
        raise ConnectionError("401 Unauthorized")

    reg = ConnectorRegistry()
    reg.register(gitlab("alpha", credential="t", reach=refuse), instance="gitlab:alpha")
    (probe,) = reg.check_health().probes
    assert probe.health is Health.FAILING
    assert "401" in probe.detail


def test_a_probe_that_raises_is_reported_and_never_propagates():
    """One broken adapter must not hide its sibling — the whole rule, in one row."""

    def explode() -> Probe:
        raise RuntimeError("an adapter bug")

    reg = ConnectorRegistry()
    reg.register(FakeConnector("broken", probe=explode))
    reg.register(FakeConnector("fine"))

    report = reg.check_health()
    by_name = {p.name: p for p in report.probes}
    assert by_name["broken"].health is Health.FAILING
    assert "an adapter bug" in by_name["broken"].detail
    assert by_name["fine"].health is Health.OK, "the sibling was hidden"
    assert not report.healthy


def test_a_probe_past_the_bound_is_failing_at_the_bound_and_abandoned():
    """CAP-35's bound, on *waiting*. The blocked probe is never cancelled."""
    release = threading.Event()
    entered = threading.Event()

    def hang() -> Probe:
        entered.set()
        release.wait(30)  # far past any bound this test would use
        return Probe("silent", Health.OK, "answered eventually — and too late")

    reg = ConnectorRegistry()
    reg.register(FakeConnector("silent", probe=hang))
    reg.register(FakeConnector("fine"))

    started = time.monotonic()
    try:
        report = reg.check_health(timeout=0.3)
        elapsed = time.monotonic() - started

        assert entered.is_set(), "the probe never ran, so nothing was bounded"
        assert elapsed < 5.0, (
            f"check_health waited {elapsed:.1f}s for a probe that never returns — "
            f"the bound is advisory, which is not a bound"
        )
        by_name = {p.name: p for p in report.probes}
        assert by_name["silent"].health is Health.FAILING
        assert "abandoned" in by_name["silent"].detail
        assert by_name["fine"].health is Health.OK, (
            "a silent connector delayed or hid a healthy one"
        )
    finally:
        release.set()


def test_the_bound_covers_the_whole_call_not_each_connector_in_turn():
    """Ten silent connectors cost one bound, not ten."""
    release = threading.Event()

    def hang() -> Probe:
        release.wait(30)
        return Probe("late", Health.OK, "too late")

    reg = ConnectorRegistry()
    for n in range(5):
        reg.register(FakeConnector(f"silent-{n}", probe=hang))

    started = time.monotonic()
    try:
        report = reg.check_health(timeout=0.3)
        elapsed = time.monotonic() - started
        assert elapsed < 1.5, f"the deadline is per connector, not per call ({elapsed:.1f}s)"
        assert all(p.health is Health.FAILING for p in report.probes)
        assert len(report.probes) == 5
    finally:
        release.set()


def test_check_health_reports_in_registration_order():
    reg = ConnectorRegistry()
    for n in ("c", "a", "b"):
        reg.register(FakeConnector(n))
    assert [p.name for p in reg.check_health().probes] == ["c", "a", "b"]


# ── The process default ──────────────────────────────────────────────────────


def test_install_replaces_rather_than_merges():
    """A second composition describes that daemon, not it plus every earlier one."""
    registry_module.install([FakeConnector("first")])
    registry_module.install([FakeConnector("second")])
    assert [c.name for c in registry_module.all_connectors()] == ["second"]


def test_install_accepts_a_built_registry_so_another_load_path_can_attach():
    built = ConnectorRegistry()
    built.register(gitlab("alpha"), instance="gitlab:alpha")
    assert registry_module.install(built) is built
    assert registry_module.default_registry() is built
    assert registry_module.all_connectors() == built.all_connectors()


def test_the_module_accessors_read_the_installed_registry():
    registry_module.install([gitlab("alpha")])
    assert registry_module.sample_events()
    assert [p.name for p in registry_module.check_health().probes] == ["gitlab:alpha"]


def test_composition_populates_the_registry(tmp_path):
    """`build()` registers what it builds — the property the AD gates rest on."""
    from pm_ai.app import wiring

    daemon = wiring.build(tmp_path, "alpha")
    assert registry_module.all_connectors()
    assert set(registry_module.default_registry().instances()) == set(daemon.connectors)
    assert list(registry_module.all_connectors()) == list(daemon.connectors.values()), (
        "the daemon holds the instances and the registry enumerates them — they "
        "must be the same objects, not two constructions of one connector"
    )
