"""People are read in one place, and the PM is asked in one place (story 2o).

Spec: `_bmad-output/specs/spec-pm-ai/stories/2o-people-are-read-as-people.md`.

AD-34: a person is resolved when a line is read, through one mapping function
over the alias table, and "is this the PM" is one function. Three rules, scanned
over every module in `pm_ai/` (not `tests/`, which fills the table on purpose):

- **The alias table.** `ALIASES` is referenced only in `pm_ai/domain/handles.py`,
  and there only in its module-level declaration, in `register_alias` (its one
  writer) and in `person_for` (its one reader). In `person_for` it may only be
  read in place — `ALIASES.get(...)`, `ALIASES[...]`, `... in ALIASES` — so a
  write, a mutating method, or binding it to another name (`t = ALIASES`) is a
  violation. Anywhere else, module level of `handles.py` included, a reference
  is a violation — imported under another name, reached as an attribute, or
  fetched with `getattr(module, "ALIASES")`.
- **The PM.** `pm_identity` is called only by `pm_ai.app.wiring.read_pm_identity`.
  Any other reference — a call, an import under another name, the function
  passed as a value, `getattr(module, "pm_identity")` — is a violation, outside
  its own definition and `wiring`'s import of it.
- **The user-id key.** Reading the key is allowed only in `domain`'s rule
  (`usable_user_id`); `recorded_user_id` delegates to it. A read is a subscript
  load (`row[USER_ID_KEY]`, `row["user_id"]`), `.get`, `.pop` or `.setdefault`
  of the key (also as `dict.get(row, KEY)`), a membership test (`KEY in row`),
  or `operator.itemgetter(KEY)`. The key is recognised spelled as the literal
  `"user_id"` or as `USER_ID_KEY` under any import name. A write
  (`row[USER_ID_KEY] = ...`) or a dict literal is not a read.

Heuristics, stated: the scan has no types, so a dict reached through
`vars(module)` or a key built at runtime is not seen.
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Iterator
from pathlib import Path

from conftest import PACKAGE_ROOT, SourceFile, alias_map, canonical_name, source_files

HANDLES = PACKAGE_ROOT / "domain" / "handles.py"
WIRING = PACKAGE_ROOT / "app" / "wiring.py"
ENROLMENT = PACKAGE_ROOT / "core" / "connector_enrolment.py"

TABLE = "ALIASES"
TABLE_FUNCTIONS = frozenset({"register_alias", "person_for"})
PM = "pm_identity"
PM_CALLER = "read_pm_identity"
KEY_NAME = "USER_ID_KEY"
KEY_TEXT = "user_id"
KEY_READER_IN_DOMAIN = "usable_user_id"

def _same(path: Path, other: Path) -> bool:
    return path.resolve() == other.resolve()


def _origin(f: SourceFile, name: str) -> str:
    return f.aliases.get(name, name)


def _last(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1]


def _named(f: SourceFile, node: ast.AST, target: str) -> bool:
    """Whether `node` refers to `target`, through an import alias or an attribute."""
    if isinstance(node, ast.Name):
        return node.id == target or _last(_origin(f, node.id)) == target
    if isinstance(node, ast.Attribute):
        return node.attr == target
    return False


def _getattr_of(node: ast.AST, target: str) -> bool:
    """`getattr(x, "target")` (or `setattr`, `delattr`) with the name as a literal."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"getattr", "setattr", "delattr", "hasattr"}
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == target
    )


def _walk(tree: ast.AST) -> Iterator[tuple[ast.AST, tuple[str, ...], bool]]:
    """Every node with its enclosing function names, and whether it is module level."""

    def visit(node: ast.AST, stack: tuple[str, ...]) -> Iterator[tuple[ast.AST, tuple[str, ...], bool]]:
        for child in ast.iter_child_nodes(node):
            yield child, stack, not stack
            inner = stack
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = (*stack, child.name)
            yield from visit(child, inner)

    yield from visit(tree, ())


def _imports(node: ast.AST, target: str) -> bool:
    return isinstance(node, ast.ImportFrom) and any(a.name == target for a in node.names)


# ── Rule 1: the alias table ──────────────────────────────────────────────────


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _is_declaration(node: ast.AST, parents: dict[ast.AST, ast.AST], module_level: bool) -> bool:
    """`ALIASES = ...` or `ALIASES: ... = ...` at module level, as the target."""
    parent = parents.get(node)
    return (
        module_level
        and isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Store)
        and isinstance(parent, (ast.Assign, ast.AnnAssign))
        and parents.get(parent) is not None
        and isinstance(parents[parent], ast.Module)
    )


def _read_in_place(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """`ALIASES.get(...)`, `ALIASES[k]` as a load, or `k in ALIASES`."""
    if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
        return False
    parent = parents.get(node)
    if isinstance(parent, ast.Attribute) and parent.attr == "get":
        return isinstance(parents.get(parent), ast.Call) and parents[parent].func is parent
    if isinstance(parent, ast.Subscript) and parent.value is node:
        return isinstance(parent.ctx, ast.Load)
    if isinstance(parent, ast.Compare) and node in parent.comparators:
        return all(isinstance(op, (ast.In, ast.NotIn)) for op in parent.ops)
    return False


def table_violations(files: list[SourceFile]) -> list[str]:
    found: list[str] = []
    for f in files:
        in_handles = _same(f.path, HANDLES)
        parents = _parents(f.tree)
        for node, stack, module_level in _walk(f.tree):
            hit = _imports(node, TABLE) or _getattr_of(node, TABLE)
            if not hit and isinstance(node, (ast.Name, ast.Attribute)):
                hit = _named(f, node, TABLE)
            if not hit:
                continue
            if in_handles:
                if _is_declaration(node, parents, module_level):
                    continue
                if stack and stack[0] == "register_alias":
                    continue
                if stack and stack[0] == "person_for" and _read_in_place(node, parents):
                    continue
            found.append(
                f"{f.location(node)} — touches the alias table `{TABLE}`; "
                f"`register_alias` is its one writer and `person_for` its one "
                f"reader, reading it in place (AD-34, story 2o)"
            )
    return found


# ── Rule 2: the PM ───────────────────────────────────────────────────────────


def pm_violations(files: list[SourceFile]) -> list[str]:
    found: list[str] = []
    for f in files:
        in_wiring = _same(f.path, WIRING)
        for node, stack, _module_level in _walk(f.tree):
            if _imports(node, PM):
                if in_wiring:
                    continue  # the one caller's import
                found.append(f"{f.location(node)} — imports `{PM}`; ask `read_pm_identity`")
                continue
            hit = _getattr_of(node, PM)
            if not hit and isinstance(node, (ast.Name, ast.Attribute)):
                hit = _named(f, node, PM)
            if not hit:
                continue
            if in_wiring and stack and stack[0] == PM_CALLER:
                continue
            found.append(
                f"{f.location(node)} — refers to `{PM}` outside "
                f"`pm_ai.app.wiring.{PM_CALLER}`, its one caller (story 2o)"
            )
    return found


# ── Rule 3: the user-id key ──────────────────────────────────────────────────


def _is_key(f: SourceFile, node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return node.value == KEY_TEXT
    return _named(f, node, KEY_NAME)


_KEYED_METHODS = frozenset({"get", "pop", "setdefault"})


def _reads_key(f: SourceFile, node: ast.AST) -> bool:
    if isinstance(node, ast.Subscript):
        return isinstance(node.ctx, ast.Load) and _is_key(f, node.slice)
    if isinstance(node, ast.Compare):
        return _is_key(f, node.left) and any(
            isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
        )
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _KEYED_METHODS:
        # `dict.get(row, KEY)` puts the key second; `row.get(KEY)` first.
        unbound = isinstance(func.value, ast.Name) and func.value.id == "dict"
        index = 1 if unbound else 0
        return len(node.args) > index and _is_key(f, node.args[index])
    if _last(canonical_name(f, node)) == "itemgetter":
        return any(_is_key(f, arg) for arg in node.args)
    return False


def key_violations(files: list[SourceFile]) -> list[str]:
    found: list[str] = []
    for f in files:
        in_handles = _same(f.path, HANDLES)
        for node, stack, _module_level in _walk(f.tree):
            if not _reads_key(f, node):
                continue
            if in_handles and stack and stack[0] == KEY_READER_IN_DOMAIN:
                continue
            found.append(
                f"{f.location(node)} — reads the user-id key; apply "
                f"`pm_ai.domain.handles.{KEY_READER_IN_DOMAIN}`, the one rule "
                f"(story 2o)"
            )
    return found


# ── The scans over `pm_ai/` ──────────────────────────────────────────────────


def test_the_alias_table_has_one_writer_and_one_reader():
    found = table_violations(source_files())
    assert not found, "the alias table reached off its two functions:\n  " + "\n  ".join(found)


def test_pm_identity_is_called_only_by_read_pm_identity():
    found = pm_violations(source_files())
    assert not found, "pm_identity reached off its one caller:\n  " + "\n  ".join(found)


def test_the_user_id_key_is_read_only_through_the_one_rule():
    found = key_violations(source_files())
    assert not found, "the user-id key read off the one rule:\n  " + "\n  ".join(found)


def test_every_exempted_place_exists():
    """An exemption naming nothing would let the scan pass for the wrong reason."""
    for path in (HANDLES, WIRING, ENROLMENT):
        assert path.is_file(), f"{path} moved; the exemption names nothing"
    handles = ast.parse(HANDLES.read_text(encoding="utf-8"))
    wiring = ast.parse(WIRING.read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(handles) if isinstance(n, ast.FunctionDef)}
    assert {*TABLE_FUNCTIONS, PM, KEY_READER_IN_DOMAIN} <= defined
    assert PM_CALLER in {n.name for n in ast.walk(wiring) if isinstance(n, ast.FunctionDef)}
    declared = {
        target.id
        for node in handles.body  # type: ignore[attr-defined]
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }
    assert TABLE in declared, f"{TABLE} is not declared at module level in {HANDLES}"
    enrolment = ast.parse(ENROLMENT.read_text(encoding="utf-8"))
    assert "recorded_user_id" in {
        n.name for n in ast.walk(enrolment) if isinstance(n, ast.FunctionDef)
    }, "recorded_user_id moved; the delegation the key rule relies on is gone"


# ── The scans' own failures, demonstrated ────────────────────────────────────


def _planted(source: str, where: Path = PACKAGE_ROOT / "core" / "planted.py") -> list[SourceFile]:
    tree = ast.parse(textwrap.dedent(source))
    return [SourceFile(path=where, tree=tree, aliases=alias_map(tree))]


def _lines(found: list[str]) -> list[str]:
    return [line.split(" — ")[0] for line in found]


def test_a_planted_alias_table_read_is_named_with_its_file_and_line():
    found = table_violations(
        _planted(
            """
            from pm_ai.domain.handles import ALIASES as table
            import pm_ai.domain.handles as handles

            a = table.get(h)
            b = handles.ALIASES[h]
            c = getattr(handles, "ALIASES")
            """
        )
    )
    assert _lines(found) == [
        "pm_ai/core/planted.py:2",
        "pm_ai/core/planted.py:5",
        "pm_ai/core/planted.py:6",
        "pm_ai/core/planted.py:7",
    ]


def test_a_planted_write_or_alias_inside_person_for_is_named():
    found = table_violations(
        _planted(
            """
            ALIASES = {}

            def person_for(handle):
                ALIASES[handle] = None
                ALIASES.clear()
                t = ALIASES
                t[handle] = None
                if handle in ALIASES:
                    return ALIASES[handle]
                return ALIASES.get(handle)

            def something_else():
                return ALIASES
            """,
            where=HANDLES,
        )
    )
    assert _lines(found) == [
        "pm_ai/domain/handles.py:5",
        "pm_ai/domain/handles.py:6",
        "pm_ai/domain/handles.py:7",
        "pm_ai/domain/handles.py:14",
    ]


def test_a_planted_module_level_write_in_handles_is_named():
    """Only the declaration is exempt at module level."""
    found = table_violations(
        _planted(
            """
            ALIASES: dict = {}
            ALIASES[key] = person
            ALIASES.update(seed)
            """,
            where=HANDLES,
        )
    )
    assert _lines(found) == ["pm_ai/domain/handles.py:3", "pm_ai/domain/handles.py:4"]


def test_a_planted_pm_identity_call_is_named_through_an_alias_or_getattr():
    found = pm_violations(
        _planted(
            """
            from pm_ai.domain import handles
            from pm_ai.domain.handles import pm_identity as who

            a = who(rows, aliased=frozenset())
            b = handles.pm_identity(rows, aliased=frozenset())
            c = getattr(handles, "pm_identity")
            """
        )
    )
    assert _lines(found) == [
        "pm_ai/core/planted.py:3",
        "pm_ai/core/planted.py:5",
        "pm_ai/core/planted.py:6",
        "pm_ai/core/planted.py:7",
    ]


def test_pm_identity_in_wiring_outside_read_pm_identity_is_named():
    found = pm_violations(
        _planted(
            """
            from pm_ai.domain.handles import pm_identity

            def read_pm_identity(storage):
                return pm_identity((), aliased=frozenset())

            def elsewhere():
                return pm_identity((), aliased=frozenset())
            """,
            where=WIRING,
        )
    )
    assert _lines(found) == ["pm_ai/app/wiring.py:8"]


def test_a_planted_user_id_read_is_named_in_every_spelling():
    found = key_violations(
        _planted(
            """
            from pm_ai.domain.connector_rows import USER_ID_KEY as KEY
            import pm_ai.domain.connector_rows as rows
            import operator
            from operator import itemgetter as pick

            a = row["user_id"]
            b = row.get(KEY)
            c = row[rows.USER_ID_KEY]
            d = row.pop(KEY)
            e = KEY in row
            f = dict.get(row, KEY)
            g = row.setdefault("user_id", "")
            h = operator.itemgetter(KEY)
            i = pick("user_id")
            row[KEY] = "written, not read"
            j = {KEY: "a literal, not a read"}
            """
        )
    )
    assert _lines(found) == [f"pm_ai/core/planted.py:{n}" for n in range(7, 16)]


def test_the_key_is_exempt_nowhere_but_the_rule():
    """`connector_enrolment` and `read_pm_identity` read nothing of their own now."""
    for where in (ENROLMENT, WIRING):
        found = key_violations(
            _planted(
                """
                def recorded_user_id(row):
                    return row.get("user_id")

                def read_pm_identity(storage):
                    return row["user_id"]
                """,
                where=where,
            )
        )
        assert len(found) == 2, found
