"""Tests for the enforcement helpers themselves.

The AST helpers had no tests, which is how two load-bearing rules came to be
bypassable without anything turning red: `_write_mode` read the mode from the
builtin's argument position only, and call names were never resolved through
import aliases. Both were found on 2026-08-19 by planting violations and
watching a green suite.

A check nobody checks is a comment.
"""

from __future__ import annotations

import ast

from conftest import SourceFile, alias_map, canonical_name
from test_static_rules import SHELL_CALLS, _mode_of, _write_mode


def _call(src: str) -> ast.Call:
    return next(n for n in ast.walk(ast.parse(src)) for n in [n] if isinstance(n, ast.Call))


def _file(src: str) -> SourceFile:
    tree = ast.parse(src)
    return SourceFile(path=__import__("pathlib").Path("x.py"), tree=tree, aliases=alias_map(tree))


class TestWriteModeDetection:
    """AD-5 — both call shapes carry the mode in different positions."""

    def test_builtin_open_write(self):
        assert _write_mode(_call('open(p, "w")'))

    def test_builtin_open_read(self):
        assert not _write_mode(_call("open(p)"))
        assert not _write_mode(_call('open(p, "r")'))

    def test_path_open_write_is_not_scored_as_a_read(self):
        """The regression. `Path.open` puts the mode first, and this returned False."""
        assert _write_mode(_call('Path(p).open("w")'))
        assert _write_mode(_call('p.open("a")'))
        assert _write_mode(_call('p.open("wb")'))

    def test_path_open_read(self):
        assert not _write_mode(_call("p.open()"))
        assert not _write_mode(_call('p.open("r")'))

    def test_mode_keyword_either_shape(self):
        assert _write_mode(_call('open(p, mode="w")'))
        assert _write_mode(_call('p.open(mode="a")'))

    def test_mode_of_reports_the_actual_mode(self):
        """The append-only ledger check reads this, not just the boolean."""
        assert _mode_of(_call('p.open("a", encoding="utf-8")')) == "a"
        assert _mode_of(_call('open(p, "w")')) == "w"
        assert _mode_of(_call("p.open()")) == ""


class TestAliasResolution:
    """AD-1 — a banned call renamed at import is still the banned call."""

    def test_plain_import_is_unchanged(self):
        f = _file("import subprocess\nsubprocess.run(x)")
        assert canonical_name(f, _call("subprocess.run(x)")) == "subprocess.run"

    def test_aliased_module_resolves_to_its_origin(self):
        """The regression: `_sp.run` matched no forbidden-call entry."""
        f = _file("import subprocess as _sp\n_sp.run(x)")
        resolved = canonical_name(f, _call("_sp.run(x)"))
        assert resolved == "subprocess.run"
        assert resolved in SHELL_CALLS, "an alias must not evade the shell scan"

    def test_from_import_resolves_to_its_origin(self):
        f = _file("from subprocess import run\nrun(x)")
        assert canonical_name(f, _call("run(x)")) in SHELL_CALLS

    def test_from_import_with_alias(self):
        f = _file("from subprocess import run as go\ngo(x)")
        assert canonical_name(f, _call("go(x)")) in SHELL_CALLS

    def test_unrelated_names_pass_through(self):
        f = _file("import subprocess as _sp\nhelper(x)")
        assert canonical_name(f, _call("helper(x)")) == "helper"

    def test_dotted_import_does_not_remap_its_root(self):
        """The other regression: `import os.path` used to map `os → os.path`.

        Under that mapping `os.system(x)` resolved to `os.path.system`, which
        matches no forbidden-call set — one character of import style disabled
        the AD-1 shell guard in any module using a dotted import, and
        `pm_ai.platform.paths` uses one. The fix was proved by planting a
        violation and left no test behind (review 2026-08-28); this is that
        test, so the next tidy-up of `alias_map` cannot silently undo it.
        """
        f = _file("import os.path\nos.system(x)")
        resolved = canonical_name(f, _call("os.system(x)"))
        assert resolved == "os.system"
        assert resolved in SHELL_CALLS, "a dotted import must not evade the shell scan"


def test_shell_scan_covers_every_layer_that_may_not_spawn():
    """AD-1 — `app` was the one layer excluded, and the one that imports everything.

    The composition root is where a shell call is most dangerous and was where
    neither the AST scan nor .importlinter looked.
    """
    import test_static_rules

    source = ast.parse(
        __import__("pathlib").Path(test_static_rules.__file__).read_text()
    )
    fn = next(
        n
        for n in ast.walk(source)
        if isinstance(n, ast.FunctionDef)
        and n.name == "test_ad1_no_shell_execution_outside_platform"
    )
    listed = {
        el.value
        for node in ast.walk(fn)
        if isinstance(node, ast.List)
        for el in node.elts
        if isinstance(el, ast.Constant)
    }
    assert {"app", "core", "connectors", "skills", "surfaces", "storage"} <= listed
