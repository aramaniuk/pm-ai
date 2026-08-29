"""The model's consistency checks must fire under `python -O` (AD-3, AD-14, AD-44).

The domain's structures have to agree with each other, and ten checks at import
time are what establish that they do. They were bare `assert` statements, which
`-O` strips — so the daemon, running as a `launchd` agent where `-O` in a plist
is an ordinary optimisation, would have loaded a model with none of its coherence
verified. The first symptom would be a rebuild deleting Tier-2 state, or a capture
written to a path no `.gitignore` rule covers.

Two properties are tested, because either alone is satisfiable while the other
fails:

- **Mechanism** — no `assert` *statement* exists anywhere in `pm_ai/`. Checked by
  AST rather than by grep: the word appears in three comments discussing what a
  connector may not assert, and a text search cannot tell prose from code.
- **Behaviour** — each class of guard raises under `-O` against a model doctored
  into an inconsistent state. The conversion was mechanical, so the next
  mechanical tidy-up could undo it while passing every other test in this suite;
  this is what makes the property permanent rather than momentary.

Driven through a subprocess because `-O` is fixed at interpreter start and cannot
be toggled from inside a running one.
"""

from __future__ import annotations

import ast
import subprocess
import sys

import pytest

from conftest import PACKAGE_ROOT

# Each case doctors one module global into a state the guard exists to reject,
# then calls the checker. Grouped by *class* of guard rather than one per line:
# the three disjointness assertions are one property stated three ways, and a
# test per line would triple the maintenance for no extra signal.
CASES = {
    "a_key_meaning_two_paths": (
        "pm_ai.domain.scope_model",
        "_assert_declarations_agree",
        # Two scopes claiming one key at different relative paths — the collision
        # the per-scope trees exist to make representable. Built with
        # `dataclasses.replace` off a real Placement rather than constructed, so
        # the case cannot rot the next time that dataclass gains a field.
        "import dataclasses, pathlib;"
        " m.ADDRESS = {k: dict(v) for k, v in m.ADDRESS.items()};"
        " first = next(iter(m.ADDRESS));"
        " second = [k for k in m.ADDRESS if k is not first][0];"
        " sample = next(iter(m.ADDRESS[first].values()));"
        " m.ADDRESS[first]['clash.md'] = dataclasses.replace("
        "     sample, key='clash.md', relative=pathlib.Path('a/clash.md'));"
        " m.ADDRESS[second]['clash.md'] = dataclasses.replace("
        "     sample, key='clash.md', relative=pathlib.Path('b/clash.md'))",
    ),
    "a_personal_artifact_with_no_path": (
        "pm_ai.domain.scope_model",
        "_assert_declarations_agree",
        "m.PERSONAL_SUBJECT_ARTIFACTS = frozenset({'no_such_artifact.md'})",
    ),
    "a_scope_kind_with_no_tree": (
        "pm_ai.domain.scope_model",
        "_assert_declarations_agree",
        "m.SCOPE_TREES = {}",
    ),
    "an_artifact_both_tiered_and_excluded": (
        "pm_ai.domain.scope_model",
        "_assert_declarations_agree",
        "m.RETENTION_MANAGED = frozenset({'operational.db'})",
    ),
    "an_artifact_both_rebuilt_and_backed_up": (
        "pm_ai.domain.scope_model",
        "_assert_declarations_agree",
        "m.REBUILD_TARGETS = m.BACKUP_TARGETS = frozenset({'operational.db'})",
    ),
    "a_code_key_naming_no_node": (
        "pm_ai.domain.storage_tiers",
        "_assert_code_keys_are_declared",
        "m._CODE_KEYS = frozenset({'invented_by_a_constant/'})",
    ),
    "a_gitignored_artifact_naming_no_node": (
        "pm_ai.domain.storage_tiers",
        "_assert_code_keys_are_declared",
        "m.GITIGNORED = {k: frozenset({'invented_by_a_rule/'}) for k in m.GITIGNORED}",
    ),
    "a_value_in_both_ledger_vocabularies": (
        "pm_ai.domain.event_entries",
        "_assert_vocabularies_agree",
        # Story 2c's disjointness rule: one occurrence, one member. Doctored by
        # claiming a connector's value as a pm-ai action, which is the collision
        # that would leave a parser unable to say which subject a line had.
        "m.SELF_ACTION_VALUES = {'commit_pushed'}",
    ),
    "a_self_action_with_no_payload": (
        "pm_ai.domain.event_entries",
        "_assert_vocabularies_agree",
        "m.SELF_ACTION_PAYLOAD_FOR = {}",
    ),
    "two_lifecycles_sharing_a_member_name": (
        "pm_ai.domain.lifecycle",
        "_assert_lifecycles_are_distinct",
        # Story 16 adds ERROR to CommitmentState, which is exactly when this
        # guard earns its keep — so the doctored state is that collision.
        "import enum;"
        " m.ProposalState = enum.Enum('ProposalState', {'BROKEN': 'broken'})",
    ),
}


def _run_optimized(program: str) -> subprocess.CompletedProcess[str]:
    """Run `program` under `-O`, where every `assert` statement is stripped."""
    return subprocess.run(
        [sys.executable, "-O", "-c", program],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("case", sorted(CASES), ids=sorted(CASES))
def test_each_guard_still_refuses_under_optimization(case):
    """A doctored model must be refused whether or not asserts are compiled in."""
    module, checker, doctor = CASES[case]
    result = _run_optimized(
        f"import {module} as m\n"
        "assert not __debug__, 'the -O flag did not take, so this proves nothing'\n"
        f"{doctor}\n"
        "from pm_ai.domain.invariants import InconsistentModel\n"
        "try:\n"
        f"    m.{checker}()\n"
        "except InconsistentModel as refusal:\n"
        "    print('REFUSED:', refusal)\n"
        "else:\n"
        "    raise SystemExit('GUARD INERT: the doctored model was accepted')\n"
    )
    assert result.returncode == 0, (
        f"{case}: the guard did not refuse a model it exists to reject, under -O."
        f"\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "REFUSED:" in result.stdout


def test_a_coherent_model_still_imports_silently_under_optimization():
    """The fix must not make optimized mode noisy — only wrong models fail."""
    result = _run_optimized(
        "import pm_ai.domain.scope_model, pm_ai.domain.storage_tiers\n"
        "import pm_ai.domain.lifecycle\n"
        "assert not __debug__\n"
        "print('OK')\n"
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip() == "OK", (
        f"an unexpectedly chatty import under -O: {result.stdout!r}"
    )


def test_no_assert_statement_establishes_an_invariant_in_the_package():
    """The mechanism, checked by AST — the word also appears in three comments.

    A `grep` for `assert ` matches prose about what a connector may not assert,
    so it would report failures that are not there and, worse, could be "fixed"
    by rewording a comment. Only the statement matters.
    """
    offenders: list[str] = []
    for source in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        offenders += [
            f"{source.relative_to(PACKAGE_ROOT.parent)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
        ]
    assert not offenders, (
        "an `assert` statement carries an invariant the system relies on, and "
        "`python -O` deletes it: " + ", ".join(offenders)
    )
