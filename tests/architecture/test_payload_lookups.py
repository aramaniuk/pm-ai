"""Payload fields are not looked up by literal name, in the shapes scanned here (AD-27).

Spec: `_bmad-output/specs/spec-pm-ai/stories/2m-payload-line-grammar.md`.

"Missing means not recorded" is what keeps every old line readable, and it is
also what turns a misspelled lookup into a silent empty read: the dashboard read
`channel` while the writer wrote `p.channel`, and every real message would have
rendered as an actor and nothing else. So a payload field's line name is spelled
in `pm_ai/domain/payload_fields.py` alone; everyone else decodes and reads the
attribute, where a misspelling fails before anything runs.

Checks by AST rather than by text — the words belong in prose explaining the
rule, and a text search would flag the explanation. **What is scanned is a set
of shapes, not every possible lookup**:

- **the prefix** — a string literal opening with `p.` (the encoder's own
  `PREFIX`), anywhere in `pm_ai/`, an f-string's leading literal included;
- **the bare names, as a key** — a payload field name used as a subscript or a
  `.get` key on an entry's fields: `dict(<x>.fields)` itself, a dict
  comprehension over `<x>.fields`, or a name bound to either;
- **the bare names, compared** — a payload field name compared with `==` (or
  `!=`) against the key a `for` loop or comprehension takes from `<x>.fields`.

Scoped to receivers built from `.fields` because connectors read `sha` or
`message` out of a provider's JSON, and storage reads a proposal's `state` out
of its own, neither of which is a line. A lookup reached any other way — a
helper that takes the fields as a parameter, a key held in a variable — is not
seen.

A self-action field that shares a payload field's name (`title`, `channel` on
`goal_set`) is read by name, legitimately; the first reader that needs one adds
a named exemption here, with its reason, rather than weakening the scan.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from pm_ai.domain.events import PAYLOAD_FOR
from pm_ai.domain.payload_fields import PREFIX
from tests.architecture.conftest import PACKAGE_ROOT

ENCODER = PACKAGE_ROOT / "domain" / "payload_fields.py"
PAYLOAD_NAMES = frozenset(
    f.name for cls in PAYLOAD_FOR.values() for f in dataclasses.fields(cls)
)


def _is_fields(node: ast.AST) -> bool:
    """`<anything>.fields`."""
    return isinstance(node, ast.Attribute) and node.attr == "fields"


def _is_entry_fields(node: ast.AST) -> bool:
    """A mapping built from an entry's fields: `dict(<x>.fields)`, or a dict
    comprehension iterating `<x>.fields`."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dict"
        and len(node.args) == 1
        and _is_fields(node.args[0])
    ):
        return True
    return isinstance(node, ast.DictComp) and any(
        _is_fields(generator.iter) for generator in node.generators
    )


def _field_keys(tree: ast.AST) -> set[str]:
    """Names a `for` loop or comprehension binds as the key of `<x>.fields`:
    `k` in `for k, v in entry.fields`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.comprehension)) and _is_fields(node.iter):
            target = node.target
            if isinstance(target, ast.Tuple) and target.elts:
                target = target.elts[0]
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _entry_field_maps(tree: ast.AST) -> set[str]:
    """Names bound to a mapping built from `<x>.fields` anywhere in the module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_entry_fields(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _is_entry_fields(node.value)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return names


def _lookup(node: ast.AST) -> tuple[ast.AST, str] | None:
    """`(receiver, key)` for `x["key"]` or `x.get("key", ...)`, else `None`."""
    if isinstance(node, ast.Subscript):
        receiver, key = node.value, node.slice
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
    ):
        receiver, key = node.func.value, node.args[0]
    else:
        return None
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return receiver, key.value
    return None


def offenders(source: str, *, where: str) -> list[str]:
    """Every literal payload lookup in `source`, as `where:line why`."""
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith(PREFIX)
        ):
            found.append(f"{where}:{node.lineno} spells the `{PREFIX}` prefix")
    maps = _entry_field_maps(tree)
    for node in ast.walk(tree):
        lookup = _lookup(node)
        if lookup is None:
            continue
        receiver, key = lookup
        on_an_entry = _is_entry_fields(receiver) or (
            isinstance(receiver, ast.Name) and receiver.id in maps
        )
        if on_an_entry and key in PAYLOAD_NAMES:
            found.append(
                f"{where}:{node.lineno} looks up the payload field {key!r} by name"
            )
    keys = _field_keys(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not all(
            isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops
        ):
            continue
        operands = [node.left, *node.comparators]
        if not any(isinstance(o, ast.Name) and o.id in keys for o in operands):
            continue
        for operand in operands:
            if (
                isinstance(operand, ast.Constant)
                and isinstance(operand.value, str)
                and operand.value in PAYLOAD_NAMES
            ):
                found.append(
                    f"{where}:{node.lineno} compares a field key with the payload "
                    f"field {operand.value!r}"
                )
    return found


def _sources() -> list[Path]:
    # Anchored on the repository, never the working directory: a relative
    # `Path("pm_ai")` from anywhere else finds nothing and passes vacuously.
    sources = sorted(PACKAGE_ROOT.rglob("*.py"))
    assert sources, f"no sources under {PACKAGE_ROOT} — the scan would be vacuous"
    assert ENCODER in sources, f"{ENCODER} is missing — the exemption names nothing"
    return sources


def test_no_reader_looks_a_payload_field_up_by_its_literal_name():
    found = [
        hit
        for source in _sources()
        if source != ENCODER
        for hit in offenders(
            source.read_text(encoding="utf-8"),
            where=str(source.relative_to(PACKAGE_ROOT.parent)),
        )
    ]
    assert not found, (
        "payload fields are encoded and decoded by pm_ai/domain/payload_fields.py "
        "alone; decode the entry and read the attribute:\n  " + "\n  ".join(found)
    )


# ── The scan, demonstrated red on planted readers ────────────────────────────

def test_the_defect_this_replaces_is_caught():
    """`rendering.py` as it stood before `2m`."""
    planted = (
        "def line(entry):\n"
        "    fields = dict(entry.fields)\n"
        "    return fields.get('channel'), fields['excerpt']\n"
    )
    found = offenders(planted, where="planted.py")
    assert "planted.py:3 looks up the payload field 'channel' by name" in found
    assert "planted.py:3 looks up the payload field 'excerpt' by name" in found


def test_a_prefixed_lookup_is_caught_anywhere():
    planted = "def line(fields):\n    return fields['p.channel']\n"
    assert offenders(planted, where="planted.py") == [
        "planted.py:2 spells the `p.` prefix"
    ]


def test_a_prefix_built_by_an_f_string_is_caught():
    planted = "def name(field):\n    return f'p.{field}'\n"
    assert offenders(planted, where="planted.py") == [
        "planted.py:2 spells the `p.` prefix"
    ]


def test_a_direct_lookup_on_the_entry_fields_is_caught():
    planted = "def title(entry):\n    return dict(entry.fields).get('title')\n"
    assert offenders(planted, where="planted.py") == [
        "planted.py:2 looks up the payload field 'title' by name"
    ]


def test_a_comparison_against_a_loop_key_over_the_fields_is_caught():
    planted = (
        "def channel(entry):\n"
        "    for key, value in entry.fields:\n"
        "        if key == 'channel':\n"
        "            return value\n"
    )
    assert offenders(planted, where="planted.py") == [
        "planted.py:3 compares a field key with the payload field 'channel'"
    ]


def test_a_comparison_inside_a_comprehension_over_the_fields_is_caught():
    planted = (
        "def excerpt(entry):\n"
        "    return [v for k, v in entry.fields if 'excerpt' == k]\n"
    )
    assert offenders(planted, where="planted.py") == [
        "planted.py:2 compares a field key with the payload field 'excerpt'"
    ]


def test_a_dict_comprehension_over_the_fields_is_caught():
    planted = (
        "def channel(entry):\n"
        "    fields = {k: v for k, v in entry.fields}\n"
        "    direct = {k: v for k, v in entry.fields}.get('excerpt')\n"
        "    return fields['channel'], direct\n"
    )
    found = offenders(planted, where="planted.py")
    assert "planted.py:3 looks up the payload field 'excerpt' by name" in found
    assert "planted.py:4 looks up the payload field 'channel' by name" in found


def test_a_provider_json_lookup_is_not_flagged():
    """A connector reading `sha` out of GitLab's response, or storage reading a
    proposal's `state` out of its JSON, is not reading a line."""
    planted = (
        "def sha(commit, body):\n"
        "    d = json.loads(body)\n"
        "    return commit['sha'], d['state']\n"
    )
    assert offenders(planted, where="planted.py") == []
