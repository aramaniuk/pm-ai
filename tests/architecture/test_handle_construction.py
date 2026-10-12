"""A person handle is built in one module, and the alias table is not on the write path.

Spec: `_bmad-output/specs/spec-pm-ai/stories/2n-people-are-written-as-handles.md`.

AD-34: an observed event's line records what the provider said, as a person
handle `<system>:<handle>`, built by the one producer in
`pm_ai/domain/handles.py`. Before story 2n two connectors called `resolve_actor`
in three places, each choosing its own system name and key. Three refusals keep
that from coming back, scanned over every module in `pm_ai/`:

- a `PersonHandle(...)` call outside `pm_ai/domain/handles.py`;
- a `dataclasses.replace` on a handle outside it — recognised by a `system=` or
  `handle=` keyword, or by a first argument whose last name is `actor`,
  `assignee` or holds `handle`. A heuristic: the scan has no types, so a
  `replace` on a handle held under some other name is not seen. The runtime
  check in `PersonHandle.__post_init__` still refuses a non-canonical result;
- any call of `resolve_actor`. Story 2o removed it — `person_for` is the one
  reader of the alias table, and it runs when a line is read — and the scan stays
  so a write-path resolver cannot come back under the old name.

Import aliases are followed (`from ... import PersonHandle as PH`), and so are
attribute calls (`handles.PersonHandle(...)`, `identity.resolve_actor(...)`).
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from conftest import PACKAGE_ROOT, SourceFile, alias_map, canonical_name, source_files

PRODUCER = PACKAGE_ROOT / "domain" / "handles.py"
_HANDLE_NAMES = frozenset({"actor", "assignee"})


def _last(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1]


def _final_identifier(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _replaces_a_handle(call: ast.Call) -> bool:
    if any(k.arg in {"system", "handle"} for k in call.keywords):
        return True
    if call.args:
        name = _final_identifier(call.args[0]).lower()
        return name in _HANDLE_NAMES or "handle" in name
    return False


def violations(files: list[SourceFile]) -> list[str]:
    """Every forbidden construction or call, as `file:line — what`."""
    found: list[str] = []
    for f in files:
        in_producer = f.path.resolve() == PRODUCER.resolve()
        for node in ast.walk(f.tree):
            if not isinstance(node, ast.Call):
                continue
            name = canonical_name(f, node)
            if _last(name) == "resolve_actor":
                found.append(
                    f"{f.location(node)} — calls `resolve_actor`; a line records "
                    f"the provider's handle and a reader resolves it (AD-34)"
                )
            if in_producer:
                continue
            if _last(name) == "PersonHandle":
                found.append(
                    f"{f.location(node)} — constructs `PersonHandle`; build one "
                    f"through `pm_ai.domain.handles.person_handle`"
                )
            if name == "dataclasses.replace" and _replaces_a_handle(node):
                found.append(
                    f"{f.location(node)} — `dataclasses.replace` on a person "
                    f"handle; build a new one through the producer"
                )
    return found


def test_no_handle_is_built_and_no_alias_is_resolved_outside_the_producer():
    found = violations(source_files())
    assert not found, "person handles built or resolved off the one path:\n  " + "\n  ".join(found)


def test_the_producer_module_exists_where_the_scan_exempts():
    assert PRODUCER.is_file(), f"{PRODUCER} moved; the exemption names nothing"


# ── The scan's own failure, demonstrated ─────────────────────────────────────


def _planted(source: str, name: str = "planted.py") -> list[str]:
    path = PACKAGE_ROOT / "core" / name
    tree = ast.parse(textwrap.dedent(source))
    return violations([SourceFile(path=Path(path), tree=tree, aliases=alias_map(tree))])


def test_a_planted_construction_is_named_with_its_file_and_line():
    found = _planted(
        """
        from pm_ai.domain.handles import PersonHandle as PH
        import pm_ai.domain.handles as handles

        a = PH("email", "x@y")
        b = handles.PersonHandle("email", "x@y")
        """
    )
    assert [line.split(" — ")[0] for line in found] == [
        "pm_ai/core/planted.py:5",
        "pm_ai/core/planted.py:6",
    ]


def test_a_planted_replace_on_a_handle_is_named():
    found = _planted(
        """
        import dataclasses
        from dataclasses import replace as swap

        a = dataclasses.replace(event.actor, handle="other")
        b = swap(owner_handle, system="graph")
        c = swap(event, authored_by=None)
        """
    )
    assert [line.split(" — ")[0] for line in found] == [
        "pm_ai/core/planted.py:5",
        "pm_ai/core/planted.py:6",
    ]


def test_a_planted_alias_resolution_is_named_through_an_alias_or_attribute():
    found = _planted(
        """
        from pm_ai.domain import identity
        from pm_ai.domain.identity import resolve_actor as lookup

        a = identity.resolve_actor(system="gitlab", handle="x")
        b = lookup(system="gitlab", handle="x")
        """
    )
    assert [line.split(" — ")[0] for line in found] == [
        "pm_ai/core/planted.py:5",
        "pm_ai/core/planted.py:6",
    ]
