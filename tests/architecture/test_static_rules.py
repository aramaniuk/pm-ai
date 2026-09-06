"""Static (AST) enforcement of spine invariants that imports alone can't express.

`open(path, "w")` is a call, not an import, so import-linter cannot see it.
Everything in this file is checkable without running the daemon.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from conftest import (
    PACKAGE_ROOT,
    REPO_ROOT,
    calls,
    canonical_name,
    format_violations,
    source_files,
)

# Layers permitted to perform each restricted operation.
WRITE_ALLOWED = {"storage"}

# Layers that may spawn a process at all. Both are conditional, not exempt, and
# `test_ad1_a_spawning_layer_is_scanned_too` is what makes the distinction real:
# until 2026-08-28 this constant was defined here and referenced nowhere, so
# `platform` was simply absent from the scanned layer list. Three planted
# violations — os.system, exec, and subprocess.run(shell=True) in
# pm_ai/platform/vcs.py — left the whole suite green.
SHELL_ALLOWED = {"platform", "models"}

# What a spawning layer may still never do, whatever else it is permitted.
NEVER_SPAWNABLE = {"os.system", "os.popen", "os.execv", "eval", "exec"}

WRITE_CALLS = {
    "open",
    # The low-level API, added 2026-08-24 after a probe proved the guard blind to
    # it: a real file write was added to `pm_ai.platform` using `os.open` plus
    # `os.write` and this check passed. Worse, that is the idiom the codebase now
    # uses on purpose — `os.open` is how a mode survives the umask — so the one
    # shape the guard missed was the one shape in use.
    #
    # `mkdir` and `chmod` are deliberately absent. Creating a directory is not
    # opening a file for writing, and the resolver legitimately creates scope
    # directories for the writer to write into (`platform/paths.py`). Naming the
    # distinction here is the point: it was previously true by accident.
    "os.open",
    "os.write",
    "os.writev",
    "os.pwrite",
    "os.truncate",
    "os.ftruncate",
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
    # Added by story 8d, with the one exemption below. A pool is the obvious way
    # around a rule that names `threading.Thread` and nothing else, so listing it
    # is what keeps the rule from being a speed bump — but it is also how the
    # connector health bound is implemented, and that use is not cadence.
    "concurrent.futures.ThreadPoolExecutor",
    "futures.ThreadPoolExecutor",
    "ThreadPoolExecutor",
    "concurrent.futures.ProcessPoolExecutor",
    "ProcessPoolExecutor",
}

# The one call AD-9's connector rule does not cover, and why.
#
# `pm_ai/connectors/registry.py` starts one daemon thread per connector to bound
# CAP-35's ten-second health probe. That is a *deadline*, not a schedule: it runs
# once when invoked, owns no cadence, keeps no state between calls, and touches
# no cursor. A blocking socket read cannot be cancelled from outside, so a bound
# without a thread would be a bound the adapter is merely asked to honour.
#
# Keyed by relative path AND by call name. A file-wide exemption voids AD-9 for
# the whole module: measured 2026-09-04, `threading.Timer(3600, self._reharvest)`
# — a per-connector retry cadence, exactly what AD-9 forbids — passed this gate
# unnoticed while the exemption was keyed by path alone.
SCHEDULING_EXEMPT = {"connectors/registry.py": {"threading.Thread"}}


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
    assert not (set(layers) & SHELL_ALLOWED), (
        "a layer cannot be both totally banned and conditionally permitted; "
        "SHELL_ALLOWED and this list must stay disjoint"
    )
    violations = [
        f"{f.location(node)}  {name}(...)"
        for f, node, name in calls(source_files(*layers))
        if name in SHELL_CALLS
    ]
    assert not violations, format_violations(
        violations,
        "AD-1: shell execution is confined to the SHELL_ALLOWED layers, and "
        "conditionally even there. Everything else routes through an MCP skill.",
    )


@pytest.mark.parametrize("layer", sorted(SHELL_ALLOWED))
def test_ad1_a_spawning_layer_is_scanned_too(layer):
    """AD-1 — a layer permitted to spawn is *conditionally* permitted, not exempt.

    `pm_ai.models.local` may run whisper.cpp and `pm_ai.platform` may run git.
    Neither may reach a shell, evaluate a string, or replace its own image.

    The gap this closes: `platform` was never in the banned-layer list above, and
    `SHELL_ALLOWED = {"platform"}` was defined and read by nothing — so the one
    package that legitimately spawns a process was the one package with no rule
    at all. Measured on 2026-08-28 by planting `os.system("echo pwned")`,
    `exec(...)` and `subprocess.run(..., shell=True)` in `pm_ai/platform/vcs.py`:
    373 passed, 12 import contracts kept. This is the same shape as the AD-1 gap
    closed for `pm_ai.app` on 2026-08-19 — a boundary moved into an unscanned
    layer — which is why the fix is a rule rather than one more entry in a list.
    """
    violations: list[str] = []
    for f, node, name in calls(source_files(layer)):
        if name in NEVER_SPAWNABLE:
            violations.append(
                f"{f.location(node)}  {name}(...) — banned in every layer, "
                f"including one permitted to spawn"
            )
            continue
        if name not in SHELL_CALLS:
            continue
        if any(
            kw.arg == "shell" and getattr(kw.value, "value", None) is True
            for kw in node.keywords
        ):
            violations.append(
                f"{f.location(node)}  {name}(shell=True) — AD-1 class L requires "
                f"an argv list, never a command line"
            )
    assert not violations, format_violations(
        violations,
        f"AD-1: pm_ai.{layer} may spawn an allowlisted binary with an argv list "
        f"and shell=False. It may not reach a shell, evaluate a string, or "
        f"replace its own image.",
    )


def test_ad1_the_git_subcommand_set_is_closed_in_domain():
    """AD-1 — *which* git commands `platform` may run is a domain decision.

    Permitting the package to spawn `git` is not the same as permitting it to
    spawn any git. All three permitted subcommands are read-only, which is the
    property AD-1 admits this package as class L on the strength of; a `git rm`
    reached through the same helper would be an egress class change with no
    review, inside a package the scan above treats as trusted.

    Closed in `domain` for the reason AD-27's taxonomy and AD-32's verb list are:
    a set that lives where the caller lives grows by whoever is in a hurry.
    """
    # Imported directly rather than through the suite's `mod()` helper: that
    # helper turns a missing module into a *skip*, and a security rule that can
    # skip itself is the failure mode `tests/conftest.py`'s ratchet exists for.
    # `pm_ai.domain` imports nothing optional, so there is nothing to tolerate.
    from pm_ai.domain import GIT_SUBCOMMANDS

    assert GIT_SUBCOMMANDS == frozenset(
        {"rev-parse", "check-ignore", "ls-files", "--version"}
    )

    # Every subcommand the adapter actually passes must be in the set — a literal
    # the domain does not know about is the drift this test exists to catch.
    passed: set[str] = set()
    for f, node, name in calls(source_files("platform")):
        if not name.endswith("_git") or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            passed.add(first.value)
    assert passed, "no `_git(...)` call sites found — has the helper been renamed?"
    assert passed <= GIT_SUBCOMMANDS, (
        f"pm_ai.platform passes git subcommands the domain does not permit: "
        f"{sorted(passed - GIT_SUBCOMMANDS)}. Add them to "
        f"pm_ai.domain.vcs.GIT_SUBCOMMANDS, where a reviewer sees them."
    )


def test_ad9_connectors_own_no_scheduling():
    """AD-9 — a connector never runs its own thread, timer, or polling loop.

    Per-connector schedulers compete for rate limits and drift out of the
    daemon's cursor and backoff accounting.
    """
    exempted_calls_seen = set()
    violations = []
    for f, node, name in calls(source_files("connectors")):
        if name not in SCHEDULING_CALLS:
            continue
        relative = f.path.relative_to(PACKAGE_ROOT).as_posix()
        if name in SCHEDULING_EXEMPT.get(relative, frozenset()):
            exempted_calls_seen.add((relative, name))
            continue
        violations.append(f"{f.location(node)}  {name}(...)")
    assert not violations, format_violations(
        violations,
        "AD-9: connectors expose harvest(since) and nothing else. The daemon's "
        "scheduler owns cadence, cursors, and backoff.",
    )
    # The exemption has to name a file that exists, or it silently stops being an
    # exemption and starts being a typo nobody notices.
    for exempt, allowed in SCHEDULING_EXEMPT.items():
        assert (PACKAGE_ROOT / exempt).is_file(), (
            f"SCHEDULING_EXEMPT names {exempt}, which does not exist. An "
            f"exemption for a moved or renamed file is a hole in AD-9 that reads "
            f"as a considered decision."
        )
        # An exemption nothing uses is an exemption nobody re-justified. If the
        # bound stops needing a thread, the permission to start one must go too.
        for name in allowed:
            assert (exempt, name) in exempted_calls_seen, (
                f"SCHEDULING_EXEMPT permits {name} in {exempt}, which no longer "
                f"calls it. Remove the exemption rather than leaving a standing "
                f"permission for a call the code stopped making."
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
        # Named directly because `include_external_packages` cannot see it: grimp
        # does not traverse third-party source, so `msal`'s own dependency on
        # `requests` never becomes an edge from `pm_ai.core`.
        ("core", "msal"),
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


def test_ad5_encrypted_io_belongs_to_the_single_writer_alone():
    """AD-5 — only `StorageService` performs file I/O, even inside its own package.

    The check above exempts `pm_ai.storage` wholesale, so it enforces AD-5 at
    *package* granularity while AD-5 and `StorageService`'s own docstring both
    claim the *service*: "Owns every write. Nothing else opens a file for
    writing." Story 1f briefly made that false — `crypto.py` grew
    `write_encrypted`/`read_encrypted` and wrote files without going through the
    service, so the package had two writers and nothing noticed.

    The resolution was structural rather than advisory: those functions are gone,
    and the cipher is bytes-in bytes-out with no filesystem access at all. This
    test is what stops them coming back, because the reason they were tempting —
    the modes and the seal live naturally beside the cipher — has not gone away.

    Deliberately scoped to *file* I/O. `crypto.py` may read and write bytes all
    day; what it may not do is touch a path.
    """
    forbidden = WRITE_CALLS | {"read_text", "read_bytes", "Path.open"}
    violations = []
    for f in source_files("storage"):
        if f.path.name == "service.py":
            continue
        for node in ast.walk(f.tree):
            if not isinstance(node, ast.Call):
                continue
            name = canonical_name(f, node)
            if name in forbidden or name.rsplit(".", 1)[-1] in {
                "read_text", "read_bytes", "write_text", "write_bytes",
            }:
                violations.append(f"{f.location(node)}  {name}(...)")
    assert not violations, format_violations(
        violations,
        "AD-5: file I/O inside pm_ai.storage belongs to StorageService alone. A "
        "cipher takes bytes and returns bytes; the service decides what to do "
        "with a path.",
    )


def test_every_event_entry_in_the_package_satisfies_its_category_schema():
    """Runtime checks only cover producers a test actually runs.

    `wiring.py`'s path runs in one cipher test and a future producer might run in
    none, so the declared fields are checked against the *source* as well: this
    is what would have caught `registry.py` writing `key=` while its category
    declared `idempotency_key` (story 2l).
    """
    from pm_ai.domain.event_entries import SELF_ACTION_FIELDS, WRITER_OWNED_FIELDS

    checked = 0
    for source in source_files():
        path = source.path
        for node in ast.walk(source.tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "EventEntry"):
                continue
            kw = {k.arg: k.value for k in node.keywords}
            category = kw.get("category")
            # Only literal `SelfActionType.X` producers. `parse_line` passes a
            # computed category, which has no schema to check at read time.
            if not isinstance(category, ast.Attribute):
                continue
            if getattr(category.value, "id", "") != "SelfActionType":
                continue
            if "fields" not in kw or not isinstance(kw["fields"], ast.Tuple):
                continue
            member = getattr(category, "attr", "")
            declared = next(
                names for m, names in SELF_ACTION_FIELDS.items() if m.name == member
            )
            written = {e.elts[0].value for e in kw["fields"].elts} | WRITER_OWNED_FIELDS
            missing = [name for name in declared if name not in written]
            assert not missing, (
                f"{path.name} writes a {member} entry missing {missing}; the "
                f"category declares {list(declared)}."
            )
            checked += 1
    assert checked, "the scan matched no producers — it would pass on an empty package"


# ── Story 4a: the config loader interprets bytes and opens nothing ───────────
#
# The story's central Always was invisible to every declared command: the
# single-writer sweep above exempts read-mode opens, the import contracts list
# only network and database clients, and the file-I/O rule is scoped to
# `pm_ai.storage`. `load_config(raw: bytes | None)` is the real guarantee — there
# is nothing to open — and these are what turn red if a path argument and a read
# appear beside it.

# An allowlist, not a denylist, and that is the whole point. A denylist of read
# calls is only as good as the reviewer's memory: `codecs.open`,
# `importlib.resources.files`, `pkgutil.get_data` and a bare `.read()` all
# passed the first version of this check. You cannot read a file without either
# the builtin `open` (caught below) or an import, so pinning the imports is what
# closes the shape rather than the spelling. Adding a name here is a deliberate
# act; the question to answer first is whether it can reach the filesystem.
CONFIG_IMPORTS_ALLOWED = frozenset({"__future__", "collections", "dataclasses", "math", "tomllib"})

# Belt to the allowlist's braces: the builtin needs no import, and a read verb on
# an object obtained some other way should still be loud.
READ_CALLS = frozenset({
    "open",
    "load",
    "read",
    "readline",
    "readlines",
    "readinto",
    "read_text",
    "read_bytes",
    "get_data",
    "files",
    "as_file",
    "iterdir",
    "scandir",
    "listdir",
    "walk",
    "glob",
    "rglob",
})


def _import_heads(source) -> set[str]:
    """Top-level module of every import in one file, relative imports included."""
    heads: set[str] = set()
    for node in ast.walk(source.tree):
        if isinstance(node, ast.Import):
            heads |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            heads.add(f"{prefix}{(node.module or '').split('.')[0]}")
    return heads


def test_story_4a_the_config_loader_reads_no_file():
    """`pm_ai.core.config` parses bytes handed to it and reaches no filesystem."""
    modules = [f for f in source_files("core") if f.path.name == "config.py"]
    assert modules, (
        f"{PACKAGE_ROOT / 'core' / 'config.py'} is missing — this rule would "
        f"pass by scanning nothing"
    )
    (config,) = modules
    violations = [
        f"{config.location(node)}  {name}(...)"
        for _f, node, name in calls([config])
        if name.split(".")[-1] in READ_CALLS
    ]
    violations += [
        f"{config.rel} imports {module}"
        for module in sorted(_import_heads(config) - CONFIG_IMPORTS_ALLOWED)
    ]
    assert not violations, format_violations(
        violations,
        "Story 4a: pm_ai.core.config interprets bytes and opens nothing — `core` "
        "is I/O-free and StorageService.read_artifact is the single reader. If an "
        "import here is genuinely needed, add it to CONFIG_IMPORTS_ALLOWED after "
        "establishing it cannot reach the filesystem.",
    )


def test_story_4a_tomllib_is_imported_by_exactly_one_module():
    """One reader of `config.toml`, checked by import node rather than by text.

    A substring search for `tomllib` verified "mentioned in exactly one module",
    which is a different claim: any comment naming the parser broke it, and two
    real importers would have compared as an unsorted list whose outcome
    depended on filesystem order.
    """
    importers = {
        f.rel
        for f in source_files()
        for node in ast.walk(f.tree)
        if (
            isinstance(node, ast.Import)
            and any(alias.name.split(".")[0] == "tomllib" for alias in node.names)
        )
        or (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] == "tomllib"
        )
    }
    assert importers == {"pm_ai/core/config.py"}, format_violations(
        sorted(importers),
        "Story 4a: config.toml has one reader. TOML parsed anywhere else is a "
        "second interpretation of the same file, with its own idea of what the "
        "accepted keys are.",
    )


# The address `wiring.py` carried as `Daemon.pm_handle`'s default until story 4a
# gave `config.toml` a reader.
RETIRED_PM_LITERAL = "andrei@example.com"

# The maintainer's real address, which is not an example and must not become a
# fixture. Story 4a's own first draft used it as one — in the tests of the story
# whose job was to remove a personal address from the package, and invisible to
# a sweep that looked only for the literal above.
PERSONAL_DOMAIN = "itspartner.net"


def test_story_4a_no_developer_address_is_compiled_into_the_package():
    """The literal is gone, not shadowed by a config default that overrides it."""
    holders = [
        f.rel for f in source_files() if RETIRED_PM_LITERAL in f.path.read_text(encoding="utf-8")
    ]
    assert not holders, format_violations(
        holders,
        f"Story 4a: {RETIRED_PM_LITERAL} was one developer's address as a "
        f"shipped default. It comes from config.toml now; a literal here is a "
        f"machine configured by whoever wrote it.",
    )


def test_no_real_personal_address_is_used_as_a_fixture():
    """A reserved example domain in code, always — tests included.

    Swept over `tests/` as well as `pm_ai/`, because that is where the slip
    happened. This file names the domain in order to look for it, so it is the
    one file excluded — the same exemption `environment.py` gets from the sweep
    that forbids naming the encryption variable.
    """
    roots = [PACKAGE_ROOT, REPO_ROOT / "tests"]
    holders = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or path == pathlib.Path(__file__):
                continue
            if PERSONAL_DOMAIN in path.read_text(encoding="utf-8"):
                holders.append(str(path.relative_to(REPO_ROOT)))
    assert not holders, format_violations(
        holders,
        "Use a reserved example domain (example.com/.org/.net, RFC 2606). A real "
        "address in a fixture is one somebody eventually mails.",
    )
