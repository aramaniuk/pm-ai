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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pm_ai.app.pipelines import run_dashboard
from pm_ai.app.wiring import (
    Bootstrap,
    Daemon,
    application_storage,
    bootstrap,
    build,
    onboard_project,
)
from pm_ai.connectors.registry import check_health as probe_connectors
from pm_ai.core.config import Config, ConfigRefused, load_config
from pm_ai.core.project_registry import ProjectEntry
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.connectors.probe import probe_credential
from pm_ai.platform.doctor import (
    ArtifactState,
    Health,
    Presence,
    Probe,
    Report,
    config_readable,
    registry_readable,
    run_all,
)
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
        composed = _compose(keychain)
        daemon, failure = composed.daemon, composed.failure
        return dispatch(
            arguments,
            daemon=daemon,
            diagnose=lambda: _diagnose(keychain, composed),
            probe_credential=probe_credential,
            # `pm-ai connector check`'s probes, run from the one layer permitted
            # to reach `pm_ai.connectors` — `surfaces-through-core` forbids the
            # CLI from importing the registry, exactly as `os-behind-platform`
            # forbids it the doctor's probes. Passed unbound, so its own default
            # timeout stays CAP-35's bound and this module holds no second copy
            # of the number. Deliberately independent of `daemon`: the registry
            # is populated by `build()` and empty before it, and an empty
            # registry is a first-run state rather than a refusal.
            probe_connectors=lambda: probe_connectors(),
            # `23b`'s pipeline, bound to the daemon this process built. Injected
            # for `probe_connectors`' reason: `run_dashboard` reaches a
            # connector, the scope model and the single writer at once, and
            # `surfaces-through-core` forbids the CLI from importing any of the
            # three. The instant comes from `daemon.clock` rather than from a
            # `datetime.now()` here, so the day the dashboard renders and the
            # timestamps storage stamps come off the same clock.
            # `4k`'s sequence, bound to this process's keychain and nothing
            # else. Deliberately not a function of `daemon`: this is the command
            # that makes a daemon possible, so on the machine it exists for
            # composition has already stopped.
            onboard=lambda path, alias: onboard_project(keychain, path, alias),
            dashboard=_dashboard(daemon),
            # `4h`'s setup: this process's keychain, the single reader and
            # writer for `config.toml`, and probes that re-read the machine.
            first_run=_FirstRun(keychain),
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


def _dashboard(daemon: Daemon | None) -> Callable[[DataScope], Path]:
    """`run_dashboard`, bound to this process's daemon and to its clock.

    A closure rather than the pipeline itself, because the CLI may not name
    `Daemon` — `surfaces` sits below `app` — so what crosses the boundary is a
    function of a scope.

    The unbuilt case returns a callable that refuses rather than `None`. Nothing
    should ever reach it: `_dashboard` in the CLI asks `require_daemon()` first
    and gets the composition root's own sentence, which names *why* there is no
    daemon. This is the guard behind that, and it raises rather than returning a
    path a caller would print without a file behind it.
    """
    if daemon is None:
        def unavailable(scope: DataScope) -> Path:
            raise ScopeResolutionError(
                f"no daemon was built on this machine, so the {scope} dashboard "
                f"has nothing to render from. `pm-ai doctor` reports why."
            )

        return unavailable
    # `daemon.clock` rather than a `datetime.now()` composed here: the daemon was
    # built with one clock and the single writer stamps from it, so a dashboard
    # whose day boundary came from a second read would be dated against a clock
    # nothing else in the process consults (AD-5).
    return lambda scope: run_dashboard(daemon, scope=scope, now=daemon.clock())


class _FirstRun:
    """`pm-ai setup`'s reach into storage and the probes, bound to one keychain.

    The CLI names this shape structurally (`dispatch.FirstRun`) because it may
    reach neither `pm_ai.storage` nor `pm_ai.platform`. Every call builds what
    it needs when it is made rather than at construction, so a `pm-ai doctor`
    run pays nothing for a command it did not ask for.

    The writer is `application_storage`'s rather than `daemon.storage`, because
    on the machine `setup` exists for there is no daemon: composition stopped
    at "no project enrolled", which is what step 2 fixes.
    """

    def __init__(self, keychain: KeychainPort) -> None:
        self._keychain = keychain

    @property
    def keychain(self) -> KeychainPort:
        return self._keychain

    def read_config(self) -> bytes | None:
        """The pre-write read: absence is `None`, anything else unreadable raises."""
        return read_optional(
            application_storage(self._keychain), scope=APPLICATION, artifact=CONFIG_ARTIFACT
        )

    def write_config(self, payload: bytes, *, expected: bytes | None) -> Path:
        """`render_config`'s bytes through the single writer, if nothing moved.

        Read again immediately before the write and compared with what setup
        read when it started. Between the two there were prompts, and a file
        that somebody edited or another run wrote meanwhile is refused rather
        than replaced — setup refuses rather than overwrites, and a second
        writer's config is not setup's to discard.
        """
        storage = application_storage(self._keychain)
        now = read_optional(storage, scope=APPLICATION, artifact=CONFIG_ARTIFACT)
        if now != expected:
            raise ConfigRefused(
                "config.toml changed while setup was asking its questions, so "
                "nothing was written over it. Run `pm-ai setup` again: it reads "
                "the file as it is now."
            )
        return storage.write_artifact(payload, scope=APPLICATION, artifact=CONFIG_ARTIFACT)

    def diagnose(self) -> Report:
        """`1g`'s probes over a fresh read of both artifacts.

        Fresh, because the states `main` read at startup describe the machine
        before setup changed it. `run_all` alone and not `_diagnose`: the
        composition-failure probe answers "can one daemon be built", and a
        second registered project — an ordinary setup outcome — would make
        that `FAILING` for a reason no setup step can fix.
        """
        read = bootstrap(self._keychain)
        return run_all(self._keychain, config=read.config, registry=read.registry)


def read_optional(
    storage: StorageService, *, scope: DataScope, artifact: str
) -> bytes | None:
    """`read_artifact`, with absence as a value instead of an exception.

    A pass-through since story 8f, and kept only until a slice retires it.
    `StorageService.read_artifact` ended in `path.read_bytes()` and had no
    `bytes | None` form, so the first read of an optional artifact on a clean
    machine raised out of whatever asked, and this is the translation `4c` wrote
    for `config.toml` — a first run, not a failure, which `load_config(None)` is
    defined to mean. `8f` moved that form onto `StoragePort` itself, because
    every caller of an optional artifact needs it and the one that forgets to
    write its own wrapper aborts on a machine that is merely new.

    The `except` below is therefore unreachable through the real service and is
    retained for a fake that has not caught up. What has not changed is what is
    *not* translated: a directory in the way, a permission refusal or an
    unreadable device are all `OSError`s that are not absence, and reporting
    them as "no file" is how a machine that cannot read its own configuration
    looks freshly installed.
    """
    try:
        return storage.read_artifact(scope=scope, artifact=artifact)
    except FileNotFoundError:
        return None


def _bootstrap(keychain: KeychainPort) -> Bootstrap:
    """`projects.toml` and `config.toml`, read before anything is composed.

    A delegation since `4d`. The read itself lives in `pm_ai.app.wiring`,
    because it needs a bootstrap `ScopePaths.production()` and a
    `StorageService` built on it — the registry has to be read before the
    resolver that knows the projects can exist, and `wiring` is the module that
    may construct both.

    A repository still never enters the system by being found: nothing here
    searches for `.project-ai` directories, because a search would opt somebody
    else's checkout into harvesting without anyone having asked (AD-11).
    """
    return bootstrap(keychain)


def _ambiguous(projects: Mapping[str, ProjectEntry]) -> Probe:
    """Two or more enrolled projects, reported as what it is.

    Its own probe since `4d`, and the slice that made the state reachable is the
    slice that had to fix it. `_select` raises `UnknownProject`, which
    `_compose` catches in its `ScopeResolutionError` arm and reports as "the
    enrolled project cannot be resolved to a directory", remedy "Re-enrol the
    repository" — wrong in every word for an operator whose projects both
    resolve perfectly well. It was unreachable only because the registry was
    always empty, which stopped being true here.

    Choosing between them — a flag, the working directory, a default in
    `config.toml` — is still not this module's policy to invent. What changed is
    that refusing now says so.
    """
    return Probe(
        "project",
        Health.FAILING,
        f"{len(projects)} projects are registered and pm-ai has no way to "
        f"choose between them yet: {', '.join(sorted(projects))}",
        "Nothing is broken and nothing needs re-enrolling — every one of these "
        "resolves. pm-ai has no project selection yet, so until it does, one "
        "enrolled project is the supported arrangement. `pm-ai doctor` keeps "
        "working meanwhile.",
    )


@dataclass(frozen=True, slots=True)
class _Composition:
    """What composition produced, plus the two artifacts `doctor` reports on.

    The states travel separately from the daemon because they outlive it: when
    composition fails there is no `daemon.storage` to read `config.toml`
    through, and "nothing could reach it from here" is precisely the answer
    `4i`'s `UNOBTAINABLE` exists to carry. A `doctor` run that reported the
    config as absent in that case would be inventing an answer nobody gave.
    """

    daemon: Daemon | None
    failure: Probe | None
    config: ArtifactState
    registry: ArtifactState


def _compose(keychain: KeychainPort) -> _Composition:
    """The daemon, or the one probe that explains why there isn't one.

    Never raises for a reason an operator can act on. The four that reach here
    — an unreadable or unparseable registry, no project enrolled, more than one
    enrolled, a root that will not answer, a `config.toml` that will not parse —
    are reported rather than propagated, because the command most likely to be
    running is the one asking what is wrong.

    `config.toml` is read *after* the daemon exists rather than before, because
    `StorageService` is the single reader (AD-5) and there is no other legal way
    to open the file. `Config` reaches the daemon by assignment for the same
    reason: `build()` takes it as an argument, and the argument cannot be
    computed until `build()` has returned.

    `projects.toml` cannot use that arrangement, which is why it is read through
    `wiring.registered_projects` before anything else happens: `build()` needs
    the mapping, so the read cannot wait for `build()` to return.
    """
    read = _bootstrap(keychain)
    projects, registry, config = read.projects, read.registry, read.config
    if not projects:
        # The registry probe already says this, in all four of its states —
        # absent, empty, unreadable and unparseable — and says it better than a
        # second probe here could, because it is holding the bytes. Returned as
        # the failure so a refusal from `dispatch` can name the same reason.
        return _Composition(None, registry_readable(registry), config, registry)
    if len(projects) > 1:
        return _Composition(None, _ambiguous(projects), config, registry)
    (project_id,) = projects
    try:
        paths = ScopePaths.production(
            projects={pid: entry.path for pid, entry in projects.items()}
        )
        daemon = build(None, project_id, paths=paths, keychain=keychain)
    except ScopeResolutionError as unresolvable:
        return _Composition(
            None,
            Probe(
                "project", Health.FAILING,
                f"the enrolled project cannot be resolved to a directory: {unresolvable}",
                "Re-enrol the repository. Every subcommand but `doctor` needs a "
                "scope to act in, and pm-ai will not guess at one.",
            ),
            config,
            registry,
        )
    except OSError as unreadable:
        return _Composition(
            None,
            Probe(
                "pm-ai root", Health.FAILING,
                f"pm-ai's own directory could not be read or written: {unreadable}",
                "Check the ownership and permissions of ~/.pm-ai. Nothing that "
                "persists state can run until this answers.",
            ),
            config,
            registry,
        )
    # Read by `bootstrap` rather than re-read through `daemon.storage`. Both are
    # the single reader, so either is legal; reading once is what stops `doctor`
    # and the daemon from holding two different answers about one file, and it
    # is also what lets the probe report a first-run config on a machine where
    # composition never got far enough to build a daemon at all.
    if config.presence is Presence.UNREADABLE:
        # Refused rather than defaulted, and the distinction matters more than
        # it looks: `ArtifactState.unreadable` carries no bytes, so falling
        # through to `load_config(None)` would boot this machine on defaults
        # while a `config.toml` it could not open sat beside it — a setting that
        # reads as configured and has no effect, which is the one failure
        # `pm_ai.core.config` exists to prevent. An *absent* config is different
        # and does default: there is nothing there to disagree with.
        return _Composition(None, config_readable(config), config, registry)
    try:
        daemon.config = load_config(config.raw)
    except ConfigRefused:
        # The probe rather than a second `Probe(...)` built here. It reads the
        # same bytes and carries the same loader message, and one of them
        # phrased differently from the other is how `doctor` and a refusal end
        # up disagreeing about the same file.
        return _Composition(None, config_readable(config), config, registry)
    return _Composition(daemon, None, config, registry)


def _diagnose(keychain: KeychainPort, composed: _Composition) -> Report:
    """Every startup probe, plus whatever stopped the daemon being built.

    Appended rather than prepended: the probes above it are the causes — an
    incomplete install, an unreachable keychain — and a daemon that could not be
    composed is usually the consequence. An operator reading top-down meets the
    thing to fix first.

    Appended only when `run_all` has not already said it. Since `4d` and `4i`
    the report carries a probe for each of the two artifacts, and two of
    `_compose`'s failures *are* those probes — a registry nothing can parse, a
    config the loader refuses. Printing either one twice would make an operator
    look for two problems, and reconciling the two copies by hand is how they
    start to disagree.
    """
    report = run_all(keychain, config=composed.config, registry=composed.registry)
    if composed.failure is None or composed.failure.name in {p.name for p in report.probes}:
        return report
    return Report((*report.probes, composed.failure))


if __name__ == "__main__":  # pragma: no cover - the console script is the surface
    sys.exit(main())
