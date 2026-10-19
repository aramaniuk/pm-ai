"""A ratchet on skipped tests, so the suite cannot go quiet as it grows.

`mod()` in `test_domain_invariants.py` turns a missing module into a *skip*, by
design — the architecture tests were written against the package Phase 1 would
create, and skipping is how they wait for it. `conftest.py` in `architecture/`
states the intent plainly: "the Phase 1 exit criterion is zero skips in this
directory."

Nothing enforced that. The count was tracked by hand, in prose, inside story
acceptance criteria ("the skip count falls from 30 to 29") — which works only for
as long as someone remembers to look. Meanwhile every story from 1d onward adds
modules that tests reach through `mod()`, so the silent surface grows in exactly
the direction nobody watches.

This makes the number structural. It goes **down** as modules land, never up, and
the constant has to move in the same commit as the change that moves the count —
so a story that implements one module cannot also, quietly, add two new skips
elsewhere and net out even.

Deliberately a session hook rather than a test: a test only sees the skips
recorded before it runs, and this guards a property of the whole run.

The report is emitted from `pytest_unconfigure` for one specific reason. Pytest
writes its stats line ("227 passed, 30 skipped") *after* `pytest_sessionfinish`
and `pytest_terminal_summary`, and that line still reads green even once the exit
status is failing — so a message printed from either hook leaves the reader's eye
resting on the word "passed". `pytest_unconfigure` runs last, which is the only
place the verdict can be the final thing on screen. Printing a warning that the
next line contradicts would reproduce the exact failure this file exists to
prevent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# The home this test run started with — the developer's own, on a real machine.
# Read once, at import, before any test redirects `HOME`.
_REAL_HOME = Path(os.path.expanduser("~")).resolve()


@pytest.fixture(autouse=True)
def _never_the_real_pm_ai_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that resolves pm-ai's layout under the real home (story 4r).

    `ScopePaths.production()` is the one place the pm-ai home is anchored to
    `Path.home()`. A test that reaches it without redirecting `HOME` opens the
    developer's own `~/.pm-ai/private/operational.db` — and, since stores are
    prepared under their own claim, migrates it and leaves a sidecar there.
    Patched in this process only: `HOME` itself is untouched, so subprocesses
    that need it (mypy, lint-imports, `uv`) still see it.
    """
    from pm_ai.platform.paths import ScopePaths

    real = ScopePaths.production.__func__  # type: ignore[attr-defined]

    def guarded(cls, *, home=None, projects=None):
        base = Path(home) if home is not None else Path.home()
        if base.expanduser().resolve() == _REAL_HOME:
            pytest.fail(
                f"this test resolved pm-ai's home under the real home ({_REAL_HOME}); "
                f"redirect HOME to a throwaway directory first",
                pytrace=True,
            )
        return real(cls, home=home, projects=projects)

    monkeypatch.setattr(ScopePaths, "production", classmethod(guarded))


@pytest.fixture(autouse=True)
def _never_a_real_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches the Ollama server on this machine (story 7a).

    The local adapter's real transport is replaced by one that answers every
    request as "nothing is listening", so a test that goes through wiring —
    `doctor`, `setup` — meets a stopped Ollama rather than the developer's own.
    A test that wants a server injects an `httpx.MockTransport` itself.
    """
    import httpx

    from pm_ai.models.local import ollama

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "tests never reach a real Ollama: inject an httpx.MockTransport",
            request=request,
        )

    monkeypatch.setattr(
        ollama, "_default_transport", lambda _backend=None: httpx.MockTransport(refuse)
    )


RUNTIME_EXTRA = "uv sync --extra runtime"
RUNTIME_MODULES = ("starlette", "fastapi", "uvicorn")


def _require_runtime_extra() -> None:
    """Fail collection, naming the fix, when the runtime extra is not installed.

    From story 4e the suite needs it: the loopback API is served by `starlette`
    (through `fastapi`), and `test_ad8_*` and the daemon's slice exercise it. A
    missing extra used to be something the suite tolerated by importing extras
    lazily; for the API that would mean a skip, and a skip reads as coverage.
    So the run stops here instead, before a single test is collected.
    """
    import importlib

    for name in RUNTIME_MODULES:
        try:
            importlib.import_module(name)
        except ImportError as missing:
            raise ImportError(
                f"the pm-ai test suite needs the runtime extra since story 4e, and "
                f"`{name}` is not importable. Run `{RUNTIME_EXTRA}` and try again "
                f"— the suite does not skip what it cannot import."
            ) from missing


_require_runtime_extra()

# Every one of these is a `pm_ai.*` module that does not exist yet. There are no
# environment-dependent skips: the optional runtime dependencies (`keyring`,
# `sqlite_vec`) are imported inside the functions that use them precisely so a
# missing extra cannot turn a test into a skip that reads as coverage — and
# since story 4e a missing extra fails collection outright. If this
# number ever has to rise, the reason belongs in the commit message.
# Each story states its own delta, and states it in the past tense once landed.
# The number below is the sum of them, so a story that has shipped must never
# read as still owing a subtraction — a future-tense note left standing above a
# landed one is how the same delta gets applied twice.
#
# 27 → 25, story 8d: `pm_ai.connectors.registry` exists, so
# `test_ad27_connectors_only_emit_core_declared_event_types` and
# `test_ad34_connectors_do_not_mint_event_ids` run instead of skipping.
# Since story 8k (no delta) neither gate imports the registry: both read the
# connectors of the daemon `pm_ai.app.wiring.build()` returns, so what they
# depend on is `pm_ai.app.wiring` — and `pm_ai.domain.events` for AD-27.
#
# 25 → 24, story 23d: `pm_ai.core.rendering.render_project_dashboard` exists, so
# `test_ad25_project_rendering_cannot_open_the_personal_store` runs instead of
# skipping — AD-25's only runtime check, which had skipped since it was written.
#
# Deltas rather than absolutes on purpose: two stories in flight at once would
# otherwise each hard-code a number that assumes it lands first, and whichever
# merged second would quietly overwrite the other's subtraction.
# 24 → 23, story 4e: `pm_ai.surfaces.api.app` exists, so
# `test_ad8_loopback_api_rejects_unauthenticated_requests` runs instead of
# skipping. From the same story the runtime extra is required to collect at
# all (`_require_runtime_extra` above), so no skip here depends on it.
# 23 → 21, story 11b: `pm_ai.core.transcripts` exists, so
# `test_ad23_transcript_pipeline_works_without_a_live_tenant` and
# `test_ad23_transcript_without_a_meeting_is_rejected` run instead of skipping.
# 21 → 20, story 4r: `pm_ai.core.dispatch` exists, so
# `test_ad21_slow_requests_acknowledge_instead_of_blocking` runs instead of
# skipping.
EXPECTED_SKIPS = 20

_VERDICT = pytest.StashKey[str]()


@pytest.fixture(autouse=True)
def _no_real_daemon_port(monkeypatch):
    """`pm-ai doctor` in-process asks a port nothing can be listening on (story 4e).

    Its `daemon` probe connects to `127.0.0.1:<daemon_port>`, 8747 by default,
    so a `doctor` row would otherwise depend on whatever the developer has
    running there. The composition root's probe is wrapped to ask a port this
    fixture bound and closed a moment ago instead — every other argument is the
    real one, and the probe itself is the real function. Tests that exercise
    the probe against a real listener call `pm_ai.platform.doctor` directly or
    run `pm-ai` as a separate process, which this does not reach.
    """
    import socket

    from pm_ai.app import entry

    real = entry.daemon_reachable
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        closed = reserved.getsockname()[1]

    def unreachable(port, token_path, **options):
        return real(closed, token_path, **options)

    monkeypatch.setattr(entry, "daemon_reachable", unreachable)


@pytest.fixture(autouse=True)
def _restore_alias_table():
    """Put the contents of `pm_ai.domain.handles.ALIASES` back after every test.

    The table is a module dict that lives for the whole process, and
    `register_alias` refuses a remap (story 2o), so one test's alias would fail
    the next test's registration or colour its reading. Autouse because it
    costs one copy of a dict that is empty outside tests, and because a test
    that registers an alias without asking for a fixture is exactly the one
    that would leak.

    It restores the dict's *contents*, in place, through the reference taken
    before the test. A test that swaps the attribute with
    `monkeypatch.setattr(handles, "ALIASES", ...)` is undone by monkeypatch,
    which puts this same dict object back: autouse fixtures are set up first
    and so torn down last, after monkeypatch's teardown, and anything written
    into the replacement never touched this dict.
    """
    from pm_ai.domain import handles

    table = handles.ALIASES
    saved = dict(table)
    yield
    table.clear()
    table.update(saved)


def _is_whole_suite(config: pytest.Config) -> bool:
    """True only for a full, unfiltered run — the one this ratchet can judge.

    A targeted invocation (`pytest tests/architecture/test_layering.py`, or
    anything with `-k`/`-m`/`--lf`) collects a subset, so its skip count says
    nothing about the suite's. Standing down for those is what keeps the guard
    from crying wolf every time someone runs one file.
    """
    if config.option.keyword or config.option.markexpr:
        return False
    # Read through `getattr`: these come from the cacheprovider plugin, so they
    # are absent when it is disabled (`-p no:cacheprovider`), and an
    # AttributeError here would break every run rather than one.
    if getattr(config.option, "last_failed", False):
        return False
    if getattr(config.option, "failed_first", False):
        return False
    # `testpaths = ["tests"]` supplies the default, so an unfiltered run arrives
    # with either nothing or exactly that.
    return config.args in ([], ["tests"])


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None or not _is_whole_suite(session.config):
        return

    # A collection error means the suite never ran; its skip count is noise, and
    # the errors are what the operator needs to see instead.
    if session.testsfailed or reporter.stats.get("error"):
        return

    skipped = len(reporter.stats.get("skipped", []))
    if skipped == EXPECTED_SKIPS:
        return

    if skipped > EXPECTED_SKIPS:
        verdict = (
            f"SKIP RATCHET FAILED: {skipped} skipped, baseline {EXPECTED_SKIPS} "
            f"(+{skipped - EXPECTED_SKIPS}).\n"
            f"A skip is not a pass. Something that used to run no longer does, "
            f"and the run above still reported green.\n"
            f"Find it with `-rs`, then either fix it or raise EXPECTED_SKIPS in "
            f"tests/conftest.py with the reason in the commit message."
        )
    else:
        verdict = (
            f"SKIP RATCHET FAILED: {skipped} skipped, baseline {EXPECTED_SKIPS} "
            f"(-{EXPECTED_SKIPS - skipped}).\n"
            f"Fewer skips than the baseline — good news, and the ratchet needs "
            f"turning.\n"
            f"Set EXPECTED_SKIPS to {skipped} in tests/conftest.py in this same "
            f"commit, so the next regression is measured against what the suite "
            f"actually covers now."
        )

    session.config.stash[_VERDICT] = verdict
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_unconfigure(config: pytest.Config) -> None:
    verdict = config.stash.get(_VERDICT, None)
    if verdict:
        print(f"\n{verdict}")
