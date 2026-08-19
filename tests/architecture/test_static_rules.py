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


def _write_mode(node: ast.Call) -> bool:
    """True when an `open()` call opens for writing rather than reading."""
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        return any(c in str(node.args[1].value) for c in "wax+")
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return any(c in str(kw.value.value) for c in "wax+")
    return False  # bare open(p) is a read


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
    layers = ["core", "connectors", "skills", "surfaces", "storage"]
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


def test_ad5_storage_never_rewrites_a_markdown_ledger_in_place():
    """AD-5 — append-only, checked in the one layer allowed to write.

    The write-location scan deliberately exempts `storage`; that exemption is
    what makes this check necessary. A truncating open or a whole-file rewrite
    of a ledger destroys history that AD-3 Tier 1 calls truth.
    """
    LEDGERS = ("event_log", "commitments_log", "coaching_1on1_history")
    violations = []
    for f, node, name in calls(source_files("storage")):
        rendered = ast.unparse(node) if hasattr(ast, "unparse") else ""
        if not any(led in rendered for led in LEDGERS):
            continue
        if name == "open" and _write_mode(node):
            mode = next(
                (a.value for a in node.args[1:2] if isinstance(a, ast.Constant)), ""
            )
            if "a" not in str(mode):
                violations.append(f"{f.location(node)}  open(..., {mode!r}) on a ledger")
        if name.endswith("write_text") or name.endswith("write_bytes"):
            violations.append(f"{f.location(node)}  {name}(...) replaces a ledger wholesale")
    assert not violations, format_violations(
        violations,
        "AD-5: ledgers are append-only. A status change is a new entry keyed by "
        "id (AD-14), never an in-place edit.",
    )
