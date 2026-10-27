"""No model at render time (story 7d, AD-5, AD-15).

The dashboard quotes summaries a model wrote earlier; it never asks one. Three
checks, because each alone can be walked around:

- the renderer's whole import closure inside `pm_ai` reaches nothing in
  `pm_ai.models`;
- the reads `prepare_dashboard` makes for 7d name neither `Daemon` slot;
- `prepare_dashboard` renders both pages — with summary rows, records and open
  action items present, so every 7d read runs — with a `Daemon.model` and a
  `Daemon.router` that raise if anything is asked of them.

The import check stops at the renderer, not at `pm_ai.app.pipelines`: the
composition layer imports `pm_ai.app.wiring`, which builds the model adapters
for the jobs that do call a model, so its closure reaches `pm_ai.models` by
design. That is why the third check renders through the real `prepare_dashboard`.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import datetime, timedelta, timezone

from conftest import PACKAGE_ROOT

from pm_ai.app import pipelines
from pm_ai.app.pipelines import prepare_dashboard
from pm_ai.app.wiring import build
from pm_ai.core.config import Config
from pm_ai.core.meeting_summary import action_item_id
from pm_ai.domain.identity import Actor, DataScope, ScopeKind, SourceRef
from pm_ai.domain.meeting_summaries import (
    RecordedSummary,
    SummaryActionItem,
    SummaryAnswer,
    SummaryMaker,
    SummaryStateKind,
)
from pm_ai.domain.meetings import Meeting
from pm_ai.domain.proposals import Proposal

NOW = datetime(2026, 9, 9, 10, 30, tzinfo=timezone.utc)
RENDERING = "pm_ai.core.rendering"
PIPELINES = "pm_ai.app.pipelines"


def _module_path(name: str):
    relative = name.split(".")[1:]
    package = PACKAGE_ROOT.joinpath(*relative, "__init__.py")
    if package.is_file():
        return package
    module = PACKAGE_ROOT.joinpath(*relative[:-1], f"{relative[-1]}.py") if relative else None
    return module if module is not None and module.is_file() else None


def _absolute(name: str, node: ast.ImportFrom) -> str | None:
    """The module an `ImportFrom` names, a relative one resolved against `name`."""
    if node.level == 0:
        return node.module
    path = _module_path(name)
    is_package = path is not None and path.name == "__init__.py"
    parts = name.split(".")
    base = parts if is_package else parts[:-1]
    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
    if not base:
        return None
    return ".".join(base + ([node.module] if node.module else []))


def _imports(name: str) -> set[str]:
    """Every `pm_ai` module `name` imports, at any depth in its source, relative ones too."""
    path = _module_path(name)
    if path is None:
        return set()
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("pm_ai"))
        elif isinstance(node, ast.ImportFrom):
            module = _absolute(name, node)
            if module is None or not module.startswith("pm_ai"):
                continue
            found.add(module)
            # `from pm_ai.x import y` may name a submodule `pm_ai.x.y`.
            found.update(
                f"{module}.{alias.name}"
                for alias in node.names
                if _module_path(f"{module}.{alias.name}") is not None
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("pm_ai")
        ):
            found.add(node.args[0].value)
    return found


def _closure(root: str) -> set[str]:
    seen: set[str] = set()
    pending = [root]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        # A module's import runs its packages' `__init__` too.
        parts = name.split(".")
        pending.extend(".".join(parts[:end]) for end in range(2, len(parts)))
        pending.extend(_imports(name) - seen)
    return seen


def test_the_renderer_imports_nothing_from_the_model_adapters():
    closure = _closure(RENDERING)
    assert RENDERING in closure and "pm_ai.domain.renders" in closure
    reached = sorted(name for name in closure if name.startswith("pm_ai.models"))
    assert not reached, f"the dashboard's renderer reaches the model adapters: {reached}"


def test_the_composition_layer_reaches_the_model_adapters_as_the_docstring_says():
    """Pins the reason the import check stops at the renderer: if this ever
    fails, `pipelines` became model-free and the check above should cover it."""
    assert any(name.startswith("pm_ai.models") for name in _closure(PIPELINES))


def test_the_dashboards_reads_never_name_the_model_or_the_router():
    """`prepare_dashboard` and every read on its path name neither `Daemon` slot."""
    for function in (
        pipelines.prepare_dashboard,
        pipelines.personal_meeting_digests,
        pipelines.project_meeting_digests,
        pipelines._meeting_digest,  # noqa: SLF001
        pipelines._digests_over,  # noqa: SLF001
        pipelines._rows_in_window,  # noqa: SLF001
        pipelines._month_spend,  # noqa: SLF001
        pipelines._issue_notes,  # noqa: SLF001
        pipelines._read_aliases,  # noqa: SLF001
        pipelines._project_work,  # noqa: SLF001
        # Story 15d: the commitment ledgers' one read and the block built from it.
        pipelines._approved_commitment_rows,  # noqa: SLF001
        pipelines._item_decisions,  # noqa: SLF001
        pipelines.read_page_ledgers,
        pipelines.page_commitments,
    ):
        source = inspect.getsource(function)
        assert "require_model" not in source, f"{function.__name__} names require_model"
        # Any `.router`, and any `.model` but a recorded summary's `model`
        # field — whatever name the daemon is held under.
        for node in ast.walk(ast.parse(textwrap.dedent(source))):
            if not isinstance(node, ast.Attribute):
                continue
            held = node.value.id if isinstance(node.value, ast.Name) else None
            assert node.attr != "router", f"{function.__name__} reaches a router"
            assert node.attr != "model" or held == "recorded", (
                f"{function.__name__} reaches `{held}.model`"
            )


class Refuses:
    """A `Daemon.model` or `Daemon.router` that raises if anything is asked of it."""

    def __getattr__(self, name: str):
        raise AssertionError(f"the dashboard reached a model slot ({name})")

    def __call__(self, *args, **kwargs):
        raise AssertionError("the dashboard called a model slot")


def _summarized(daemon, scope: DataScope, meeting_id: str, title: str) -> None:
    """A record with a summary, its state row, and one open action item."""
    start = NOW - timedelta(hours=4)
    item = SummaryActionItem("send the notes", "Ana", "Friday")
    daemon.meetings.put(
        Meeting(
            meeting_id=meeting_id,
            title=title,
            start=start,
            duration_minutes=30,
            attendees=(Actor("u_a"),),
            scope=scope,
        )
    )
    daemon.meetings.put_summary(
        meeting_id,
        scope=scope,
        summary=RecordedSummary(
            "qwen3:8b", False, start.date(), SummaryAnswer("A short summary.", (), (item,)),
            SummaryMaker.LOCAL,
        ),
    )
    db = daemon.storage._db  # noqa: SLF001 — a fixture's row, not a code path
    db.execute(
        "INSERT OR REPLACE INTO meeting_summaries (scope, meeting_id, state, made_by, "
        "requested, model, attempts, reason, at, version, meeting_start, job) "
        "VALUES (?, ?, ?, ?, 0, NULL, 0, '', ?, 1, ?, NULL)",
        (
            str(scope), meeting_id, SummaryStateKind.SUMMARIZED.value,
            SummaryMaker.LOCAL.value, (NOW - timedelta(hours=1)).isoformat(),
            start.isoformat(),
        ),
    )
    db.commit()
    daemon.storage.stage_proposal(
        Proposal(
            proposal_id=action_item_id(scope, meeting_id, item),
            type="action_item",
            summary=item.what,
            payload={"what": item.what, "owner": item.owner, "due": item.due},
            target=None,
            cites=SourceRef.parse(f"meeting:{meeting_id}"),
            created_at=NOW,
        )
    )


def test_a_render_with_a_model_and_a_router_that_raise_succeeds(tmp_path):
    """Matrix "Model wired": every 7d read runs — the rows, the records by id, the
    open items, the ledger — and neither slot is reached."""
    daemon = build(tmp_path, "alpha", now=lambda: NOW, config=Config(display_timezone="UTC"))
    alpha = DataScope(ScopeKind.PROJECT, "alpha")
    _summarized(daemon, DataScope(ScopeKind.PERSONAL), "mtg_home", "Home sync")
    _summarized(daemon, alpha, "mtg_alpha", "Alpha sync")
    daemon.model = Refuses()  # type: ignore[assignment]
    daemon.router = Refuses()  # type: ignore[assignment]
    personal = prepare_dashboard(daemon, scope=DataScope(ScopeKind.PERSONAL), now=NOW)
    text = personal.decode("utf-8")
    assert "Home sync — A short summary." in text and "Alpha sync" in text
    assert "  - Ana: send the notes (Friday)" in text
    assert "Frontier spend this month" in text
    project = prepare_dashboard(daemon, scope=alpha, now=NOW).decode("utf-8")
    assert "Alpha sync — A short summary." in project and "Home sync" not in project
