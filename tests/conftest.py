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

import pytest

# Every one of these is a `pm_ai.*` module that does not exist yet. There are no
# environment-dependent skips: the optional runtime dependencies (`keyring`,
# `sqlcipher3`) are imported inside the functions that use them precisely so a
# missing extra cannot turn a test into a skip that reads as coverage. If this
# number ever has to rise, the reason belongs in the commit message.
EXPECTED_SKIPS = 29

_VERDICT = pytest.StashKey[str]()


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
