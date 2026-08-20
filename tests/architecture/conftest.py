"""Shared fixtures and AST helpers for the architecture test suite.

These tests enforce ARCHITECTURE-SPINE.md mechanically. They are deliberately
written against the package that Phase 1 will create: until `pm_ai/` exists they
skip, and the Phase 1 exit criterion is zero skips in this directory.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "pm_ai"

# Layer -> directory, mirroring the spine's Design Paradigm table.
LAYERS = {
    "app": PACKAGE_ROOT / "app",
    "domain": PACKAGE_ROOT / "domain",
    "core": PACKAGE_ROOT / "core",
    "ports": PACKAGE_ROOT / "ports",
    "connectors": PACKAGE_ROOT / "connectors",
    "skills": PACKAGE_ROOT / "skills",
    "storage": PACKAGE_ROOT / "storage",
    "models": PACKAGE_ROOT / "models",
    "surfaces": PACKAGE_ROOT / "surfaces",
    "platform": PACKAGE_ROOT / "platform",
}


@dataclass(frozen=True)
class SourceFile:
    """One parsed module, carrying enough context for a readable failure."""

    path: Path
    tree: ast.AST
    aliases: dict = field(default_factory=dict)

    @property
    def rel(self) -> str:
        return str(self.path.relative_to(REPO_ROOT))

    def location(self, node: ast.AST) -> str:
        return f"{self.rel}:{getattr(node, 'lineno', '?')}"


def _require_package() -> None:
    if not PACKAGE_ROOT.is_dir():
        pytest.skip(
            "pm_ai/ does not exist yet. These contracts activate as soon as "
            "Phase 1 creates the package; zero skips here is the Phase 1 exit "
            "criterion."
        )


def source_files(*layers: str) -> list[SourceFile]:
    """Parse every module in the named layers (all layers when none given)."""
    _require_package()
    roots = [LAYERS[name] for name in layers] if layers else [PACKAGE_ROOT]
    files: list[SourceFile] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text())
            files.append(SourceFile(path=path, tree=tree, aliases=alias_map(tree)))
    if not files:
        pytest.skip(f"no source files yet under {', '.join(layers) or 'pm_ai'}")
    return files


def called_name(node: ast.Call) -> str:
    """Best-effort dotted name of a call target: open, Path.write_text, os.system."""
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def alias_map(tree: ast.AST) -> dict[str, str]:
    """Local binding -> the dotted origin it came from.

    A banned call renamed at import is still the banned call. Without this,
    `import subprocess as _sp` yields the call name `_sp.run`, which matches no
    entry in any forbidden-call set — a planted `_sp.run(..., shell=True)` was
    caught by import-linter and missed by the AST scan on 2026-08-19.
    """
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mapping[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                mapping[a.asname or a.name] = f"{node.module}.{a.name}"
    return mapping


def canonical_name(f: SourceFile, node: ast.Call) -> str:
    """`called_name` with import aliases resolved back to their origin."""
    raw = called_name(node)
    head, _, rest = raw.partition(".")
    origin = f.aliases.get(head)
    if origin is None:
        return raw
    return f"{origin}.{rest}" if rest else origin


def calls(files: list[SourceFile]):
    """Yield every (SourceFile, Call, dotted_name) triple in the given files.

    The name is alias-resolved, so a forbidden call cannot be hidden behind a
    rename at the import site.
    """
    for f in files:
        for node in ast.walk(f.tree):
            if isinstance(node, ast.Call):
                yield f, node, canonical_name(f, node)


def format_violations(violations: list[str], rule: str) -> str:
    listing = "\n".join(f"  - {v}" for v in violations)
    return f"{rule}\n{listing}"


@pytest.fixture(scope="session")
def all_sources() -> list[SourceFile]:
    return source_files()
