"""The model obligation `8e` handed story `11b`, kept as a test (AD-12, AD-15).

Extraction is pattern matching (prototype path, decision 2), and the one model
call on the transcript path is story `11e`'s header reading: local-only
(`header_reading`), over a dropped file's cleaned header lines, never the body.
So the obligation is these checks:

- the transcript path's source — the pipeline, extraction, and the readers —
  imports no model client and no HTTP client, read from the source itself;
- `ModelPort.complete`, and its implementations named one by one (the local
  adapter's since story `7a`), are the only callables anywhere in `pm_ai`
  declared to accept cleaned text (`Sanitized`), so cleaned text has one door
  out;
- the drop's only `complete` call declares `TaskClass.HEADER_READING` (11e).
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from conftest import CORE_FORBIDDEN_IMPORTS, PACKAGE_ROOT, REPO_ROOT, format_violations, source_files

MODEL_CLIENTS = ("anthropic", "ollama", "httpx")
"""What the pipeline module itself may not import. `pm_ai.app.pipelines` sits in
the composition root, which may import connectors (and so, through them, an
HTTP client); the rule is about its own source, not what wiring brings in."""

READERS_AND_EXTRACTION = ("pm_ai.core.transcripts", "pm_ai.core.extraction")
PIPELINE = "pm_ai.app.pipelines"


def _path_of(module: str) -> Path:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None, f"{module} is not importable"
    return Path(spec.origin)


def _imports(path: Path) -> list[tuple[str, int]]:
    """Every module a file imports, absolute, with the line it is imported on."""
    tree = ast.parse(path.read_text())
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, node.lineno))
            # `from pm_ai.core import transcripts` imports a module too.
            found.extend((f"{node.module}.{alias.name}", node.lineno) for alias in node.names)
    return found


def _is(name: str, forbidden: str) -> bool:
    return name == forbidden or name.startswith(f"{forbidden}.")


def _closure(roots: tuple[str, ...]) -> dict[str, Path]:
    """Every `pm_ai` module the roots import, transitively, by source."""
    seen: dict[str, Path] = {}
    pending = list(roots)
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        try:
            spec = importlib.util.find_spec(module)
        except ModuleNotFoundError:
            # `from pm_ai.x import Name` also offers `pm_ai.x.Name`, which is
            # a name rather than a module when `pm_ai.x` is not a package.
            continue
        if spec is None or spec.origin is None or not spec.origin.endswith(".py"):
            continue
        seen[module] = Path(spec.origin)
        for name, _ in _imports(seen[module]):
            if name.startswith("pm_ai"):
                pending.append(name)
    return seen


def test_the_readers_and_extraction_reach_no_model_or_io_client():
    """Transitively: nothing the readers or extraction import, inside `pm_ai`,
    imports a model client, an HTTP client or `pm_ai.models`."""
    closure = _closure(READERS_AND_EXTRACTION)
    assert set(READERS_AND_EXTRACTION) <= set(closure), "the walk did not start"
    violations = [
        f"{path.relative_to(REPO_ROOT)}:{line}  import {name}"
        for path in closure.values()
        for name, line in _imports(path)
        if any(_is(name, forbidden) for forbidden in (*CORE_FORBIDDEN_IMPORTS, "pm_ai.models"))
    ]
    assert not violations, format_violations(
        violations,
        "story 11b: the transcript path is pattern matching, and no model or I/O "
        "client may be reachable from its readers or its extraction; story 11e's "
        "header reading goes through `ModelPort` from the pipeline, never from here.",
    )


def test_the_pipeline_imports_no_model_client():
    """The pipeline's own source names no model client and no `pm_ai.models`."""
    path = _path_of(PIPELINE)
    violations = [
        f"{path.relative_to(REPO_ROOT)}:{line}  import {name}"
        for name, line in _imports(path)
        if any(_is(name, forbidden) for forbidden in (*MODEL_CLIENTS, "pm_ai.models"))
    ]
    assert not violations, format_violations(
        violations,
        "story 11b: the pipeline reaches a model only through `ModelPort` "
        "(`require_model`); story 11e's header reading is its one call on the drop path.",
    )


DROP_PATH = (
    "drop_transcript",
    "drop_file",
    "preview_drop",
    "_plan_drop",
    "_header_by_model",
    "_new_meeting",
    "_preview_of",
    "_write_participants",
    "_drop_aliases",
)
"""The pipeline's functions a drop runs through (story 11e)."""


def test_the_drops_only_model_call_is_the_header_reading():
    """Story 11e: one `complete` call on the drop path, declaring `header_reading`."""
    tree = ast.parse(_path_of(PIPELINE).read_text())
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in DROP_PATH
    }
    assert set(functions) == set(DROP_PATH), sorted(set(DROP_PATH) - set(functions))
    calls = [
        (name, node)
        for name, function in functions.items()
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "complete"
    ]
    assert [name for name, _ in calls] == ["_header_by_model"], calls
    (_, call), = calls
    declared = {keyword.arg: keyword.value for keyword in call.keywords}
    task_class = declared["task_class"]
    assert isinstance(task_class, ast.Attribute) and task_class.attr == "HEADER_READING"


def _accepts_sanitized(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    arguments = function.args
    every = [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *(a for a in (arguments.vararg, arguments.kwarg) if a is not None),
    ]
    for argument in every:
        if argument.annotation is None:
            continue
        for node in ast.walk(argument.annotation):
            if isinstance(node, ast.Name) and node.id == "Sanitized":
                return True
            if isinstance(node, ast.Attribute) and node.attr == "Sanitized":
                return True
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and (
                "Sanitized" in node.value
            ):
                return True
    return False


def _qualified(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((f"{prefix}{child.name}", child))
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def test_model_port_complete_is_the_only_callable_that_accepts_cleaned_text():
    """AD-12 — cleaned text has one door out of pm-ai, and it is the model port.

    Every function or method in `pm_ai` with a parameter annotated with
    `Sanitized` is listed; the list must be exactly `ModelPort.complete` and
    the implementations of it, named one by one — since story `7a`, the local
    adapter's `complete`. A further acceptor — a skill payload, a surface
    renderer, a helper inside an adapter — would be another route for text a
    model may read, and is the place to stop and ask.
    """
    acceptors = sorted(
        f"{file.path.relative_to(PACKAGE_ROOT)}::{name}"
        for file in source_files()
        for name, function in _qualified(file.tree)
        if _accepts_sanitized(function)
    )
    # Story 7c widened this by name: the router's `complete` and the frontier
    # adapter's take the meeting's cleaned fragments for `--by claude`.
    assert acceptors == [
        "models/frontier/anthropic.py::AnthropicFrontier.complete",
        "models/local/ollama.py::OllamaModel.complete",
        "models/router.py::ModelRouter.complete",
        "ports/__init__.py::ModelPort.complete",
    ], acceptors


def test_the_acceptor_scan_sees_an_annotated_parameter():
    """The scan above is only as good as its matcher: a planted acceptor is found."""
    planted = ast.parse(
        "class Leaky:\n"
        "    def post(self, *, body: 'Sanitized') -> None: ...\n"
        "def send(fragments: Sequence[Sanitized]) -> None: ...\n"
        "def fine(text: str) -> None: ...\n"
    )
    found = [name for name, function in _qualified(planted) if _accepts_sanitized(function)]
    assert found == ["Leaky.post", "send"]
