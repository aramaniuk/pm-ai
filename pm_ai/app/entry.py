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

A fourth outcome leaves no daemon either and is **not** one of those three, so
it produces no probe: several projects are enrolled and nothing chose between
them (story 4l). That is a refusal at dispatch, carried on
`_Composition.undecided`, and `doctor` reports it as the non-fault it is — a
report line naming which project this invocation binds to, or saying that the
working directory names none. Reporting it as a failure is how `doctor` and
`setup` came to disagree about a machine with nothing wrong with it.

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
from dataclasses import dataclass, replace
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
from pm_ai.core.goal_register import ARTIFACT as GOALS_ARTIFACT
from pm_ai.core.goal_register import (
    GOALS_SCOPE,
    GoalRegister,
    GoalUnrecorded,
    MalformedGoals,
    parse_goals,
    set_goal,
)
from pm_ai.domain.event_entries import (
    DAEMON_ACTOR,
    EventEntry,
    MalformedEntry,
    SelfActionType,
    render_entry,
)
from pm_ai.domain.goals import Goal
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
from pm_ai.platform.paths import ScopePaths
from pm_ai.ports import ArtifactBusy, KeychainPort, StoragePort
from pm_ai.storage.service import StorageService
from pm_ai.surfaces.cli.dispatch import EXIT_REFUSAL, EXIT_UNEXPECTED, dispatch

__all__ = ["CONFIG_ARTIFACT", "GoalBook", "GoalWritten", "main", "read_optional"]

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
            # `22b`'s writer over the daemon's own single writer. `None` with no
            # daemon, and `goal set` asks `require_daemon()` first, so the
            # refusal it meets names why there is none.
            goals=None if daemon is None else GoalBook(daemon.storage, channel="cli"),
            # What stopped the daemon being built, so a refusal can name it.
            # `config.toml` is the case that needs it: `4j`'s matrix requires
            # `pm-ai config show` to report the loader's own message, and this
            # probe's detail is the only place it survives.
            unavailable=None if failure is None else failure.detail,
            # `4l`'s refusal, which is not a fault and must not be dressed as
            # one: several projects are enrolled, the working directory is
            # inside none of them, and pm-ai declines to pick. Carried
            # separately from `unavailable` because that field's sentence sends
            # the operator to `pm-ai doctor` — which, on this machine, correctly
            # reports nothing wrong.
            undecided=composed.undecided,
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


@dataclass(frozen=True, slots=True)
class GoalWritten:
    """What one `GoalBook.set` did, read off the file it merged onto.

    `revised` comes from the fresh read `set` makes, not from anything a
    surface read before its prompts — a goal someone else set meanwhile is a
    revision, and saying "set" over it would be false. `path` is `None` exactly
    when `changed` is false: nothing was written, so there is no write to name.
    """

    path: Path | None
    revised: bool
    changed: bool


# How long the writer's own stamps make a line, measured with stand-ins of the
# same length: `evt_` and twenty hex digits, and a microsecond UTC isoformat.
_PROBE_ID = "evt_" + "0" * 20
_PROBE_AT = "0000-00-00T00:00:00.000000+00:00"


class GoalBook:
    """`strategic_goals.md`, read and set through the single reader and writer.

    The CLI names this shape structurally (`dispatch.GoalBook`) because
    `surfaces` may not reach storage (AD-30). `core` renders and opens nothing,
    so the read, the write and the event-log entry are all made here — and
    Telegram, the second channel, will hold one of these with its own `channel`.
    """

    def __init__(self, storage: StoragePort, *, channel: str) -> None:
        self._storage = storage
        self._channel = channel

    def read(self) -> GoalRegister:
        """The register as the file says now; `MalformedGoals` if it cannot say."""
        return parse_goals(self._raw(), scope=GOALS_SCOPE)

    def set(self, goal: Goal) -> GoalWritten:
        """Merge `goal` into the file, write it, then record the act (CAP-10).

        Everything that can refuse runs before the write: the file is parsed and
        merged (`set_goal` refuses a file the parser refuses, so a hand-broken
        `strategic_goals.md` stays byte-identical), and the entry is built and
        rendered at full length, so a title too long for one ledger line is a
        `MalformedGoals` with nothing written rather than a `MalformedEntry`
        after the file already changed.

        A goal identical to the one on file is a no-op: no write, no entry. Only
        the append can fail after the write, and that is `GoalUnrecorded`, which
        says the file changed and the log did not.
        """
        raw = self._raw()
        current = parse_goals(raw, scope=GOALS_SCOPE).get(goal.goal_id)
        if current == goal:
            return GoalWritten(path=None, revised=True, changed=False)
        payload = set_goal(raw, goal)
        entry = EventEntry(
            category=SelfActionType.GOAL_SET,
            actor=DAEMON_ACTOR,
            fields=(
                ("goal_id", goal.goal_id),
                ("domain", goal.domain.value),
                ("horizon", goal.horizon.value),
                ("title", goal.title),
                ("channel", self._channel),
            ),
        )
        _assert_recordable(entry)
        path = self._storage.write_artifact(payload, scope=GOALS_SCOPE, artifact=GOALS_ARTIFACT)
        try:
            self._storage.append_event_log(entry, scope=GOALS_SCOPE)
        except (OSError, ArtifactBusy, ValueError) as unrecorded:
            raise GoalUnrecorded(
                f"{goal.goal_id} WAS written to {path}, but its goal_set entry "
                f"could not be appended to the personal event log, so the log "
                f"does not record this change: {unrecorded}"
            ) from unrecorded
        return GoalWritten(path=path, revised=current is not None, changed=True)

    def _raw(self) -> bytes | None:
        return self._storage.read_artifact(scope=GOALS_SCOPE, artifact=GOALS_ARTIFACT)


def _assert_recordable(entry: EventEntry) -> None:
    """Refuse, before any write, an entry the ledger writer would refuse after it."""
    stamped = replace(
        entry,
        entry_id=_PROBE_ID,
        fields=(("ingested_at", _PROBE_AT), *entry.fields),
    )
    try:
        # `render_entry` is the writer's own check, `MAX_ENTRY_LENGTH` included,
        # so this refuses exactly what `append_event_log` would.
        render_entry(stamped)
    except MalformedEntry as refused:
        raise MalformedGoals(
            f"this goal cannot be recorded in the event log, so nothing was "
            f"written: {refused}"
        ) from refused


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


@dataclass(frozen=True, slots=True)
class _Selection:
    """Which project this invocation is about, or why nothing chose one.

    A value rather than `str | None`, because `None` was answering three
    different questions with one silence: outside every enrolled project, two
    ids sharing one directory, and a working directory that cannot be read. All
    three produced the one sentence "this command was not run inside any
    enrolled project", which is false of the second — the operator is standing
    *inside* a project — and says nothing at all about the third. Each refusal
    is now composed where its cause is known.
    """

    project_id: str | None
    refusal: str | None

    @classmethod
    def acting(cls, project_id: str) -> _Selection:
        return cls(project_id, None)

    @classmethod
    def undecided(cls, refusal: str) -> _Selection:
        return cls(None, refusal)


def _acting_project(projects: Mapping[str, ProjectEntry]) -> _Selection:
    """Which project this invocation is about, or a refusal naming why not.

    AD-11: "the CLI, when run inside a registered repository, binds to that
    project scope". That was the rule from the start and had never been
    implemented — `_ambiguous` stood here until `4l` and refused to assemble at
    all, which made a second enrolled project a `FAILING` probe on a machine
    where nothing was wrong.

    **One enrolled project answers from anywhere.** Nobody with a single project
    has to stand anywhere in particular, and asking them to would be this slice
    taxing every existing installation for a choice that does not exist on it.

    **By folder containment, and deliberately not by asking git.** A project is
    onboarded at a directory and is not required to be a repository —
    `onboard_project` runs no `git init` and checks for no working tree — so
    `working_tree` would answer `None` for a perfectly good project and refuse
    the command from inside it.

    **Innermost wins.** One project's directory may sit inside another's, and
    the nearest enclosing one is the project the operator is standing in; the
    outer one is merely an ancestor. Two enclosing projects at the *same*
    directory is not a depth to break — it is one directory under two ids, which
    `onboard_project` refuses to create — so it is refused here too rather than
    settled alphabetically.

    Nothing here is ever a guess, and every refusal names its own cause.
    """
    if len(projects) == 1:
        (only,) = projects
        return _Selection.acting(only)
    try:
        # Resolved, because the registry's paths are (`_resolved` at onboarding)
        # and a shell's `cwd` may reach the same directory through a symlink —
        # `/tmp` on macOS being the everyday one.
        here = Path.cwd().resolve()
    except OSError as unreadable:
        # A deleted or unreadable working directory. Its own refusal, because
        # "run this from inside a project" is advice the operator cannot act on
        # without first being told that the directory they are in is gone — and
        # an unstated cause is one they will hunt for in the registry.
        return _Selection.undecided(_no_working_directory(projects, unreadable))
    matched = {
        project_id: root
        for project_id, root in (
            (project_id, _folder(entry.path)) for project_id, entry in projects.items()
        )
        if root is not None and here.is_relative_to(root)
    }
    if not matched:
        return _Selection.undecided(_outside_every_project(projects, here))
    innermost = max(len(root.parts) for root in matched.values())
    named = sorted(
        project_id
        for project_id, root in matched.items()
        if len(root.parts) == innermost
    )
    if len(named) == 1:
        return _Selection.acting(named[0])
    # Two ids at one directory. Every enclosing folder is an ancestor of the
    # same path and therefore totally ordered by prefix, so equal depth means
    # equal directory — a hand-edited registry, since `project add` refuses it.
    return _Selection.undecided(_one_directory_two_ids(named, matched[named[0]]))


def _folder(path: Path) -> Path | None:
    """One enrolled path, normalised for comparison; `None` if it cannot be.

    `resolve()` is not strict, so a registry naming a directory that has since
    moved away still answers — it simply contains no working directory, and the
    registry probe is what reports it as needing attention. `OSError` is a
    symlink loop or an unreadable parent, which is "this one cannot be matched"
    rather than a reason to stop matching the others.
    """
    try:
        return path.resolve()
    except OSError:
        return None


def _enrolled_list(projects: Mapping[str, ProjectEntry]) -> str:
    """Every enrolled project by id and directory, in one clause.

    A refusal that says "pm-ai will not choose" and then does not say what the
    choices are has moved the guesswork to the operator rather than resolved it
    — and the directory is the actionable half, since every remedy here is to
    stand in one of them.
    """
    return ", ".join(f"{pid} ({projects[pid].path})" for pid in sorted(projects))


def _no_selection_is_a_fault() -> str:
    """The clause every one of these refusals ends on, spelled once.

    None of the three is a broken machine, and an operator who reads a refusal
    and then runs `pm-ai doctor` must not find the two describing different
    worlds — the disagreement this slice exists to end. Deliberately narrower
    than "doctor will say this machine is healthy", which is a verdict about the
    rest of the machine that nothing here measured.
    """
    return (
        "Every one of these projects is enrolled and watched, and none of this "
        "is a fault — `pm-ai doctor` reports it as a note and not a failure. "
        "There is no way to name a project on the command line yet, and writing "
        "to the wrong one is worse than not writing."
    )


def _outside_every_project(projects: Mapping[str, ProjectEntry], here: Path) -> str:
    """The refusal for a command run outside every enrolled project.

    **Not a probe, and that is the whole point of where it lives.** Until `4l`
    this state was `Probe("project", FAILING, ...)`, which `doctor` appended to
    its report and `setup` did not — so the two commands disagreed about the
    same machine, one exiting 0 and the other 4. Nothing here is broken: every
    project resolves, and what is missing is a decision only the operator can
    make. A refusal is where a deliberate no belongs; a probe is where a fault
    does.

    **A registry path that cannot be resolved is named rather than dropped.**
    `_folder` returns `None` for a symlink loop or an unreadable parent, and
    such an entry silently matches nothing — so an operator standing inside that
    very project would be told they were inside none, with no hint that their
    own entry is the reason. The others still work, which is why this is a
    sentence in the refusal rather than a failure of composition.
    """
    unresolvable = sorted(
        pid for pid, entry in projects.items() if _folder(entry.path) is None
    )
    named = (
        ""
        if not unresolvable
        else (
            f" pm-ai could not resolve the registered directory of "
            f"{', '.join(unresolvable)}, so that entry matched nothing here — if "
            f"you are standing inside one of those, its path in projects.toml is "
            f"what to fix."
        )
    )
    return (
        f"this command was run in {here}, which is inside none of the "
        f"{len(projects)} enrolled projects, so pm-ai will not choose one for "
        f"you: {_enrolled_list(projects)}. Run it again from inside the "
        f"project's own directory — a sub-directory of it counts.{named} "
        f"{_no_selection_is_a_fault()}"
    )


def _one_directory_two_ids(named: Sequence[str], directory: Path) -> str:
    """The refusal for two enrolled ids sharing the directory the operator is in.

    Its own sentence since the 4l review, because the general one was false
    here: it opened "this command was not run inside any enrolled project" and
    told an operator standing *inside* two of them to go and stand inside one.
    The remedy is in `projects.toml`, not in the shell.
    """
    return (
        f"{directory} is registered under {len(named)} project ids at once — "
        f"{', '.join(named)} — so standing in it does not say which project "
        f"this command is about. `pm-ai project add` refuses to create that, so "
        f"it is a hand-edit: remove or re-point the duplicate entry in "
        f"projects.toml. Picking between them by name would be pm-ai deciding "
        f"which project a write belongs to, and writing to the wrong one is "
        f"worse than not writing."
    )


def _no_working_directory(
    projects: Mapping[str, ProjectEntry], unreadable: OSError
) -> str:
    """The refusal for a working directory that cannot be read at all.

    Deleted under a running shell, or on an unmounted volume. Its own sentence
    because the cause is invisible from the general one: an operator told to
    "run it from inside a project" would look at `projects.toml`, where nothing
    is wrong, rather than at the directory they are standing in.
    """
    return (
        f"pm-ai could not read this process's working directory, so it cannot "
        f"tell which project this command is about: {unreadable}. That "
        f"directory has usually been deleted or unmounted under the shell — "
        f"`cd` somewhere that exists, inside one of the {len(projects)} "
        f"enrolled projects: {_enrolled_list(projects)}. "
        f"{_no_selection_is_a_fault()}"
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
    undecided: str | None = None
    """Why no project was selected, when nothing failed (story 4l).

    Separate from `failure` because it is not one: several projects are
    enrolled, they all resolve, and the working directory is inside none of
    them. `_diagnose` appends `failure` to the report and must not append this —
    `doctor` reporting a fault here is precisely how `doctor` and `setup` came
    to disagree about a machine with nothing wrong with it.
    """


def _compose(keychain: KeychainPort) -> _Composition:
    """The daemon, or the one probe that explains why there isn't one.

    Never raises for a reason an operator can act on. The four that reach here
    — an unreadable or unparseable registry, no project enrolled, a root that
    will not answer, a `config.toml` that will not parse — are reported rather
    than propagated, because the command most likely to be running is the one
    asking what is wrong.

    A fifth outcome is not a failure at all and is carried separately:
    several projects enrolled and a working directory inside none of them. That
    was the `len(projects) > 1` refusal until `4l`, which assembled against one
    project and gave up on two — so the second project's work was never looked
    at, and the state was reported as a fault. Every enrolled project is watched
    now, and what the working directory decides is only which project the
    *command* is about.

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
    selected = _acting_project(projects)
    project_id = selected.project_id
    if project_id is None:
        # Several enrolled and nothing chose between them. No daemon, and no
        # probe either: a command that has nowhere to act is refused, and there
        # is nothing here for `doctor` to fix. `selected.refusal` names which of
        # the three reasons this was.
        return _Composition(
            None, None, config, registry, undecided=selected.refusal
        )
    try:
        paths = ScopePaths.production(
            projects={pid: entry.path for pid, entry in projects.items()}
        )
        # Every enrolled project is watched (AD-10); `project_id` is only which
        # one this command acts in. The resolver was already handed all of them.
        daemon = build(
            None,
            project_id,
            watched=sorted(projects),
            paths=paths,
            keychain=keychain,
        )
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

    Since the `4l` review the report also carries `_selection_probe`, which says
    which project *this invocation* binds to. Without it `doctor` reported a
    healthy machine on which the very next command would refuse, and the
    operator had nothing on the page connecting the two.
    """
    report = run_all(keychain, config=composed.config, registry=composed.registry)
    probes = report.probes
    selection = _selection_probe(composed)
    if selection is not None:
        probes = (*probes, selection)
    if composed.failure is None or composed.failure.name in {p.name for p in probes}:
        return Report(probes)
    return Report((*probes, composed.failure))


SELECTION_PROBE = "project selection"
"""The probe naming which project this invocation acts in (story 4l)."""


def _selection_probe(composed: _Composition) -> Probe | None:
    """Which project this command bound to, or that the directory chose none.

    **Always `OK`, in both states, and that is deliberate rather than lenient.**
    `Report.healthy` is "every probe is `OK`", so any other value would make the
    working directory decide `doctor`'s exit code — and since `setup` reports
    through `run_all` alone, the two commands would disagree again for a new
    reason. Ambiguity is a refusal, not a fault; this line exists so the
    operator can *see* the refusal coming, not to grade the machine.

    `None` when no project is enrolled at all: the registry probe already says
    so, and a second line about selecting between nothing is noise on the one
    machine where the report is most read.
    """
    if composed.daemon is not None:
        project_id = composed.daemon.scope.project_id
        return Probe(
            SELECTION_PROBE,
            Health.OK,
            f"this command acts in {project_id}, chosen by the working directory",
            "",
        )
    if composed.undecided is None:
        return None
    return Probe(
        SELECTION_PROBE,
        Health.OK,
        "no project is selected here, so every command but this one is refused",
        composed.undecided,
    )


if __name__ == "__main__":  # pragma: no cover - the console script is the surface
    sys.exit(main())
