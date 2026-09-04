"""Story 8b — the live credential check, asserted directly.

Nothing imported this module before: the one CLI row that looked like coverage
asserted a substring (`"no credential probe"`) that both the real adapter's
refusal and `dispatch._no_probe`'s fallback contain, so deleting the injection
line in `pm_ai.app.entry` left the suite green with the CLI on a probe that
never asks anything.
"""

from __future__ import annotations

import pytest

from pm_ai.connectors.probe import PROBES, probe_credential
from pm_ai.ports import ProbeFailed, UnknownConnectorSystem


def test_a_system_with_no_probe_is_refused_by_name():
    with pytest.raises(UnknownConnectorSystem) as refused:
        probe_credential("nosuch", "a-token")
    assert "nosuch" in str(refused.value)
    assert "gitlab" in str(refused.value), "the known systems are named"


def test_gitlab_refuses_while_its_transport_is_a_stub():
    """The load-bearing refusal.

    `GitLabConnectorAdapter.reach` opens no socket, so there is nothing that
    could honestly answer. Returning success here would seal a credential on
    the strength of a check that never happened — the single failure story 8b's
    whole write ordering exists to prevent, reintroduced by the probe itself.
    """
    with pytest.raises(ProbeFailed) as refused:
        probe_credential("gitlab", "glpat-anything")
    assert "stub" in str(refused.value)


def test_no_probe_returns_success_today():
    """Every declared system refuses; none may claim a credential is good."""
    for system in PROBES:
        with pytest.raises(ProbeFailed):
            probe_credential(system, "a-token")


def test_a_refusal_never_carries_the_credential():
    secret = "glpat-do-not-print-me-123456"
    for system in (*PROBES, "nosuch"):
        with pytest.raises((ProbeFailed, UnknownConnectorSystem)) as refused:
            probe_credential(system, secret)
        assert secret not in str(refused.value)


def test_the_cli_is_wired_to_this_adapter_and_not_to_the_refusing_default():
    """The injection line, which no assertion observed.

    Deleting `probe_credential=probe_credential` from `pm_ai.app.entry` left the
    CLI on `dispatch._no_probe` and every test still passed, because the only
    row on that path matched a substring common to both messages.
    """
    import inspect

    from pm_ai.app import entry
    from pm_ai.surfaces.cli import dispatch

    source = inspect.getsource(entry.main)
    assert "probe_credential=probe_credential" in source, (
        "the composition root stopped injecting the real probe, so the CLI "
        "silently fell back to the one that refuses everything"
    )
    assert entry.probe_credential is probe_credential
    assert entry.probe_credential is not dispatch._no_probe
