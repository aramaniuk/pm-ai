"""The console script: build, then hand the built daemon to the CLI (AD-30).

`[project.scripts] pm-ai = "pm_ai.app.entry:main"`. Everything a subcommand
touches is constructed here, because this is the one layer permitted to
construct anything — `pm_ai.surfaces.cli.dispatch` sits *below* `pm_ai.app` in
the enforced layer stack and may reach neither `wiring.build` nor an adapter.

`main(argv=None)` takes its arguments rather than reading `sys.argv` inside, so
the CLI's behaviour is a unit test rather than a subprocess.

## Composition may fail, and `doctor` still has to run

`pm-ai doctor` is the command an operator runs when the machine is broken, so a
broken machine must not be what makes it unreachable. Three things can stop the
daemon being built — no project enrolled, a root that cannot be read or written,
a `config.toml` that will not parse — and each becomes **one probe result**
appended to the report rather than a traceback out of a command that exists to
survive exactly this. Nothing here loads `config.toml` before deciding what to
run, which is what keeps a broken config from hiding a broken machine.

## Why this module may import `pm_ai.platform.doctor`

`.importlinter` forbids `pm_ai.app -> subprocess` even through an intermediary,
and the probes reach `git` through `pm_ai.platform.vcs`. The contract already
carries the same structural exception for `pm_ai.app.wiring -> pm_ai.platform.vcs`
and for the same reason: `pm_ai.app` exists to construct what lives in
`pm_ai.platform`, so forbidding the import leaves the diagnostics with no legal
caller. What the exception does *not* relax is
`test_ad1_no_shell_execution_outside_platform`, which scans this package for a
`subprocess` call of its own.
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Mapping, Sequence
from pathlib import Path

from pm_ai.app.wiring import Daemon, build
from pm_ai.connectors.registry import check_health as probe_connectors
from pm_ai.core.config import Config, ConfigRefused, load_config
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.platform.doctor import Health, Probe, Report, run_all
from pm_ai.platform.keychain import MacOSKeychainAdapter
from pm_ai.platform.paths import ScopePaths, UnknownProject
from pm_ai.ports import KeychainPort
from pm_ai.storage.service import StorageService
from pm_ai.surfaces.cli.dispatch import EXIT_REFUSAL, EXIT_UNEXPECTED, dispatch

__all__ = ["CONFIG_ARTIFACT", "main", "read_optional"]

CONFIG_ARTIFACT = "config.toml"

APPLICATION = DataScope(ScopeKind.APPLICATION)


def main(argv: Sequence[str] | None = None) -> int:
    """Build what can be built, dispatch `argv`, and return an exit code.

    The outermost guard in the process. Two things are caught here and nowhere
    else:

    - `SystemExit`, so `main()` *returns* a code rather than unwinding past its
      caller. Nothing in `dispatch` raises one today, but a library below might,
      and a `main()` that sometimes returns and sometimes exits is not the thing
      an explicit `argv` was for.
    - every other exception, as exit 1 with a traceback on stderr — distinct
      from a usage error (2) and from a refusal (3), which is the distinction an
      operator's `pm-ai doctor || alert` rests on.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        keychain = MacOSKeychainAdapter()
        daemon, failure = _compose(keychain)
        return dispatch(
            arguments,
            daemon=daemon,
            diagnose=lambda: _diagnose(keychain, failure),
            # `pm-ai connector check`'s probes, run from the one layer permitted
            # to reach `pm_ai.connectors` — `surfaces-through-core` forbids the
            # CLI from importing the registry, exactly as `os-behind-platform`
            # forbids it the doctor's probes. Passed unbound, so its own default
            # timeout stays CAP-35's bound and this module holds no second copy
            # of the number. Deliberately independent of `daemon`: the registry
            # is populated by `build()` and empty before it, and an empty
            # registry is a first-run state rather than a refusal.
            probe_connectors=lambda: probe_connectors(),
            # What stopped the daemon being built, so a refusal can name it.
            # `config.toml` is the case that needs it: `4j`'s matrix requires
            # `pm-ai config show` to report the loader's own message, and this
            # probe's detail is the only place it survives.
            unavailable=None if failure is None else failure.detail,
        )
    except SystemExit as requested:
        code = requested.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        # `sys.exit("message")` is the interpreter's own convention for a fatal
        # error: the sentence goes to stderr and the status is failure. Reporting
        # it as a usage error would tell the operator they mistyped, and discard
        # the one sentence saying what actually happened.
        print(code, file=sys.stderr)
        return EXIT_UNEXPECTED
    except KeyboardInterrupt:
        # Not an `Exception`, so the guard below never saw it: Ctrl-C during a
        # ten-second connector probe produced a raw traceback and a status
        # outside the five this table declares. Interrupting is a deliberate
        # stop, which is what the refusal code means.
        print("\npm-ai: interrupted", file=sys.stderr)
        return EXIT_REFUSAL
    except Exception:
        traceback.print_exc()
        return EXIT_UNEXPECTED


def read_optional(
    storage: StorageService, *, scope: DataScope, artifact: str
) -> bytes | None:
    """`read_artifact`, with absence as a value instead of an exception.

    `StorageService.read_artifact` ends in `path.read_bytes()` and has no
    `bytes | None` form, so the first read of an optional artifact on a clean
    machine raises out of whatever asked. For `config.toml` that is a first run,
    not a failure — `load_config(None)` is defined to mean exactly this — and
    the same is true of every optional artifact `4i` and `4h` will read.

    Only `FileNotFoundError` is translated. A directory in the way, a permission
    refusal or an unreadable device are all `OSError`s that are *not* absence,
    and reporting them as "no file" is how a machine that cannot read its own
    configuration looks freshly installed.
    """
    try:
        return storage.read_artifact(scope=scope, artifact=artifact)
    except FileNotFoundError:
        return None


def _registered_projects() -> Mapping[str, Path]:
    """The enrolled projects, from the registry `pm-ai project add` writes (AD-11).

    Empty, and honestly so: `projects.toml` has no reader until `4d`, which is
    the slice that also writes it. A repository may not enter the system by
    being found — searching the filesystem for `.project-ai` directories would
    opt somebody's repository into harvesting without anyone asking — so an
    unread registry is an empty one rather than a guess.

    Until `4d` lands this makes `doctor` the only usable subcommand on every
    machine, which is precisely the state this module is built to survive.
    """
    return {}


def _select(projects: Mapping[str, Path]) -> str:
    """Which enrolled project this invocation acts on.

    One registered project needs no choosing. More than one does, and the choice
    — a flag, the working directory, a default in `config.toml` — belongs to the
    slice that owns the registry. Refusing is what keeps this module from
    inventing a policy another one is going to make.
    """
    if len(projects) == 1:
        return next(iter(projects))
    raise UnknownProject(
        f"{len(projects)} projects are registered and pm-ai has no way to choose "
        f"between them yet: {sorted(projects)}. Until it does, one enrolled "
        f"project is the supported arrangement."
    )


def _compose(keychain: KeychainPort) -> tuple[Daemon | None, Probe | None]:
    """The daemon, or the one probe that explains why there isn't one.

    Never raises for a reason an operator can act on. The three that reach here
    — an unenrolled or unresolvable project, a root that will not answer, a
    `config.toml` that will not parse — are reported rather than propagated,
    because the command most likely to be running is the one asking what is
    wrong.

    `config.toml` is read *after* the daemon exists rather than before, because
    `StorageService` is the single reader (AD-5) and there is no other legal way
    to open the file. `Config` reaches the daemon by assignment for the same
    reason: `build()` takes it as an argument, and the argument cannot be
    computed until `build()` has returned.
    """
    projects = _registered_projects()
    if not projects:
        return None, Probe(
            "project",
            Health.ABSENT,
            "no project is enrolled, so pm-ai has nothing to act on",
            "No project can be enrolled on this build yet: `pm-ai project add "
            "<path>` is story 4k and is not implemented, so this is the "
            "expected state rather than something to repair. Projects enter the "
            "system through that registry and never by being found on disk "
            "(AD-11), so an empty registry means no work has been offered yet.",
        )
    try:
        paths = ScopePaths.production(projects=projects)
        daemon = build(None, _select(projects), paths=paths, keychain=keychain)
        daemon.config = _config(daemon.storage)
    except ScopeResolutionError as unresolvable:
        return None, Probe(
            "project", Health.FAILING,
            f"the enrolled project cannot be resolved to a directory: {unresolvable}",
            "Re-enrol the repository. Every subcommand but `doctor` needs a "
            "scope to act in, and pm-ai will not guess at one.",
        )
    except ConfigRefused as refused:
        return None, Probe(
            "config.toml", Health.FAILING,
            f"config.toml says something pm-ai will not act on: {refused}",
            "Fix or remove the offending key. An unreadable config is refused "
            "rather than ignored, because a setting that reads as configured "
            "while having no effect stays wrong forever.",
        )
    except OSError as unreadable:
        return None, Probe(
            "pm-ai root", Health.FAILING,
            f"pm-ai's own directory could not be read or written: {unreadable}",
            "Check the ownership and permissions of ~/.pm-ai. Nothing that "
            "persists state can run until this answers.",
        )
    return daemon, None


def _config(storage: StorageService) -> Config:
    """`config.toml`, interpreted — absent and empty both meaning defaults."""
    return load_config(read_optional(storage, scope=APPLICATION, artifact=CONFIG_ARTIFACT))


def _diagnose(keychain: KeychainPort, failure: Probe | None) -> Report:
    """Every startup probe, plus whatever stopped the daemon being built.

    Appended rather than prepended: the probes above it are the causes — an
    incomplete install, an unreachable keychain — and a daemon that could not be
    composed is usually the consequence. An operator reading top-down meets the
    thing to fix first.
    """
    report = run_all(keychain)
    return report if failure is None else Report((*report.probes, failure))


if __name__ == "__main__":  # pragma: no cover - the console script is the surface
    sys.exit(main())
