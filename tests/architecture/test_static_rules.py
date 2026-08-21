"""Static (AST) enforcement of spine invariants that imports alone can't express.

`open(path, "w")` is a call, not an import, so import-linter cannot see it.
Everything in this file is checkable without running the daemon.
"""

from __future__ import annotations

import ast

import pytest

from conftest import calls, format_violations, source_files

# Layers permitted to perform each restricted operation.
WRITE_ALLOWED = {"storage"}
SHELL_ALLOWED = {"platform"}

WRITE_CALLS = {
    "open",
    "write_text",
    "write_bytes",
    "Path.write_text",
    "Path.write_bytes",
    "os.remove",
    "os.unlink",
    "os.rename",
    "os.replace",
    "shutil.copy",
    "shutil.copy2",
    "shutil.move",
    "shutil.rmtree",
}

SHELL_CALLS = {
    "os.system",
    "os.popen",
    "os.execv",
    "subprocess.run",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.check_call",
    "subprocess.check_output",
    "eval",
    "exec",
}

SCHEDULING_CALLS = {
    "asyncio.create_task",
    "asyncio.ensure_future",
    "loop.create_task",
    "threading.Thread",
    "threading.Timer",
    "sched.scheduler",
}


def _mode_of(node: ast.Call) -> str:
    """The mode string an `open` call was given, or "" when it took none.

    Two call shapes put it in two places: the builtin `open(path, "w")` carries
    mode second, while `path.open("w")` — `Path.open` — carries it first.

    Reading only the second position scored every `Path.open("w")` as a read.
    That is the idiomatic form in this codebase, so AD-5's single-writer rule
    passed a planted violation until 2026-08-19. Distinguish on the call shape:
    a bare `ast.Name` func is the builtin, an `ast.Attribute` is the method.
    """
    index = 1 if isinstance(node.func, ast.Name) else 0
    if len(node.args) > index and isinstance(node.args[index], ast.Constant):
        return str(node.args[index].value)
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return ""


def _write_mode(node: ast.Call) -> bool:
    """True when an `open()` call opens for writing rather than reading."""
    return any(c in _mode_of(node) for c in "wax+")  # no mode at all is a read


def test_ad5_single_writer_owns_all_file_writes():
    """AD-5 — no component outside pm_ai.storage opens a file for writing.

    Two components writing the same ledger is how half-written commitment
    entries and lost status transitions happen.
    """
    layers = [name for name in ("core", "domain", "connectors", "skills", "models", "surfaces", "platform", "app")]
    violations = []
    for f, node, name in calls(source_files(*layers)):
        if name == "open" and not _write_mode(node):
            continue
        if name in WRITE_CALLS or name.endswith(".write_text") or name.endswith(".write_bytes"):
            violations.append(f"{f.location(node)}  {name}(...)")
    assert not violations, format_violations(
        violations,
        "AD-5: file writes are the storage service's alone. Submit a typed write "
        "operation instead of writing directly.",
    )


def test_ad1_no_shell_execution_outside_platform():
    """AD-1 — the LLM core is granted zero shell capability.

    A `subprocess.run` reachable from a model-driven code path is the exact hole
    the MCP execution firewall exists to close.
    """
    # `app` is deliberately included: it is the composition root and the ONE
    # layer permitted to import every other, which made it the one layer where an
    # unscanned `subprocess.run(shell=True)` was invisible to both this check and
    # .importlinter. It passed a planted violation until 2026-08-19.
    layers = ["app", "domain", "core", "ports", "connectors", "skills", "surfaces", "storage"]
    violations = [
        f"{f.location(node)}  {name}(...)"
        for f, node, name in calls(source_files(*layers))
        if name in SHELL_CALLS
    ]
    # pm_ai.models.local is class L under AD-1: it may spawn whisper.cpp, but only
    # as an allowlisted absolute path with shell=False. os.system/popen and eval/exec
    # remain banned there too — only the subprocess family is carved out.
    for f, node, name in calls(source_files("models")):
        if name not in SHELL_CALLS:
            continue
        in_local = "local" in f.path.parts
        if in_local and name.startswith("subprocess."):
            if any(
                kw.arg == "shell" and getattr(kw.value, "value", None) is True
                for kw in node.keywords
            ):
                violations.append(f"{f.location(node)}  {name}(shell=True) — AD-1 class L requires shell=False")
            continue
        violations.append(f"{f.location(node)}  {name}(...)")
    assert not violations, format_violations(
        violations,
        "AD-1: shell execution is confined to pm_ai.platform and, for whisper.cpp "
        "only, pm_ai.models.local (class L — allowlisted absolute path, argv list, "
        "shell=False). Everything else routes through an MCP skill.",
    )


def test_ad9_connectors_own_no_scheduling():
    """AD-9 — a connector never runs its own thread, timer, or polling loop.

    Per-connector schedulers compete for rate limits and drift out of the
    daemon's cursor and backoff accounting.
    """
    violations = [
        f"{f.location(node)}  {name}(...)"
        for f, node, name in calls(source_files("connectors"))
        if name in SCHEDULING_CALLS
    ]
    assert not violations, format_violations(
        violations,
        "AD-9: connectors expose harvest(since) and nothing else. The daemon's "
        "scheduler owns cadence, cursors, and backoff.",
    )


def test_ad11_no_filesystem_discovery_of_projects():
    """AD-11 — projects enter the system only via `pm-ai project add`.

    Scanning for `.project-ai` directories would silently opt a repository into
    telemetry harvesting.
    """
    violations = []
    for f, node, name in calls(source_files()):
        if "registry" in f.path.name:
            continue  # the registry legitimately reads its own file
        if name.split(".")[-1] not in {"glob", "rglob", "walk", "iglob", "scandir"}:
            continue
        literal = " ".join(
            str(a.value) for a in node.args if isinstance(a, ast.Constant)
        )
        if ".project-ai" in literal or "project-ai" in literal:
            violations.append(f"{f.location(node)}  {name}({literal!r})")
    assert not violations, format_violations(
        violations,
        "AD-11: no auto-discovery. Read the explicit registry in ~/.pm-ai/.",
    )


def test_ad24_event_log_is_not_a_debug_sink():
    """AD-24 — `event_log.md` carries domain truth; diagnostics go elsewhere.

    Debug noise in the audit trail destroys its value as a decision record.
    """
    violations = []
    for f in source_files():
        for node in ast.walk(f.tree):
            if not isinstance(node, ast.Call):
                continue
            target = ast.unparse(node) if hasattr(ast, "unparse") else ""
            if "event_log" not in target:
                continue
            if any(level in target for level in (".debug(", ".info(", ".warning(", ".error(")):
                violations.append(f"{f.location(node)}  {target[:90]}")
    assert not violations, format_violations(
        violations,
        "AD-24: write diagnostics to ~/.pm-ai/logs/ (structured JSON, rotating). "
        "event_log.md is append-only domain truth.",
    )


@pytest.mark.parametrize(
    "layer,forbidden",
    [
        ("core", "httpx"),
        ("core", "requests"),
        ("core", "sqlite3"),
        ("core", "anthropic"),
        ("core", "ollama"),
    ],
)
def test_ad1_core_stays_io_free(layer, forbidden):
    """AD-1 — belt and braces alongside .importlinter, with a per-import failure.

    import-linter reports the contract; this reports the exact line, which is
    what you actually need when the build goes red.
    """
    violations = []
    for f in source_files(layer):
        for node in ast.walk(f.tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n == forbidden or n.startswith(f"{forbidden}.") for n in names):
                violations.append(f"{f.location(node)}  import {forbidden}")
    assert not violations, format_violations(
        violations, f"AD-1: pm_ai.{layer} must not import {forbidden}."
    )


LEDGERS = ("event_log", "commitments_log", "coaching_1on1_history")

# Words this codebase uses for a Tier-1 path when the artifact key itself is a
# parameter: `self._segment(scope, artifact, at).open(...)` names no ledger.
LEDGER_SHAPES = ("segment", "ledger")


def _ledger_names() -> frozenset[str]:
    """Every token that counts as naming a ledger in a call's source text.

    The scan below reads the *text* of the call, and the idiomatic way to spell
    an artifact key is a constant: `self._segment(scope, EVENT_LOG, at)` contains
    no `event_log`, so both event-log writes were skipped before the mode check
    ran and a planted truncating `open` passed this test on 2026-08-21. A name
    bound anywhere in the package to a string that names a ledger therefore
    counts as naming it — the binding may be renamed, but not without renaming
    the string it holds.

    This is still a text match, which is why the behavioural guard
    (`test_the_event_log_is_appended_to_never_rewritten`) is the one that cannot
    be blinded by a refactor.
    """
    names = set(LEDGERS) | set(LEDGER_SHAPES)
    for f in source_files():
        for node in ast.walk(f.tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            value = node.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            if any(led in value.value for led in LEDGERS):
                names.update(t.id for t in targets if isinstance(t, ast.Name))
    return frozenset(names)


def test_ad5_storage_never_rewrites_a_markdown_ledger_in_place():
    """AD-5 — append-only, checked in the one layer allowed to write.

    The write-location scan deliberately exempts `storage`; that exemption is
    what makes this check necessary. A truncating open or a whole-file rewrite
    of a ledger destroys history that AD-3 Tier 1 calls truth.
    """
    ledger_names = _ledger_names()
    violations = []
    for f, node, name in calls(source_files("storage")):
        rendered = ast.unparse(node) if hasattr(ast, "unparse") else ""
        if not any(led in rendered for led in ledger_names):
            continue
        if name == "open" and _write_mode(node):
            mode = _mode_of(node)  # both call shapes, per _mode_of
            if "a" not in mode:
                violations.append(f"{f.location(node)}  open(..., {mode!r}) on a ledger")
        if name.endswith("write_text") or name.endswith("write_bytes"):
            violations.append(f"{f.location(node)}  {name}(...) replaces a ledger wholesale")
    assert not violations, format_violations(
        violations,
        "AD-5: ledgers are append-only. A status change is a new entry keyed by "
        "id (AD-14), never an in-place edit.",
    )
