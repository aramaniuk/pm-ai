"""Arguments in, exit codes out — the CLI's whole job (AD-7, AD-30).

This module maps a subcommand onto a call and formats what comes back. It
constructs nothing, opens nothing, and schedules nothing: everything it needs
arrives as an argument, because `surfaces` sits *below* `app` in the enforced
layer stack and therefore cannot reach the composition root that builds adapters.
`pm_ai.app.entry` is the other half, and the split is what the layering leaves —
the untestable part (real adapters, a real keychain) is one small module there,
and the part with all the branches is this one, which performs no I/O beyond
printing.

## The exit-code table lives here and nowhere else

Three slices map outcomes to process exit codes. Leaving each to choose its own
convention makes `pm-ai doctor || alert` and `pm-ai dashboard || retry` behave
differently for the same class of outcome, and an operator cannot tell "pm-ai
refused" from "pm-ai broke" — which is the distinction the codes exist for.

    0  success
    1  an unexpected exception — a bug, with a traceback on stderr
    2  usage: no subcommand, an unknown one, or a group with no leaf
    3  a refusal: a stated, deliberate no
    4  a probe ran and what it asked about is not healthy

`8b` and `23b` reuse these values and may not add to the table. `4j` added three
leaves and, deliberately, not a sixth code: `connector check` reuses `4` for the
same reading `doctor` gives it — something was probed and the answer was not
`OK`.

## No scheduler, ever

Every subcommand runs once and exits (AD-7, enforced by `cli-owns-no-scheduling`
in `.importlinter`). The 07:00 tick belongs to the daemon.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

from pm_ai.core.config import Config, ConfigRefused, load_config, render_config
from pm_ai.core.enrolment import KeyAlreadyEnrolled, enrol
from pm_ai.core.goal_register import (
    GOALS_SCOPE,
    GoalRegister,
    GoalUnrecorded,
    MalformedGoals,
    declare_goal,
)
from pm_ai.domain.goals import Goal
from pm_ai.domain.event_entries import MalformedEntry, UnknownCategory
from pm_ai.domain.health import Report
from pm_ai.core.project_registry import RegistryRefused
from pm_ai.domain.claims import ClaimHeld
from pm_ai.domain.identity import DataScope, ScopeKind
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.core.connector_enrolment import (
    MalformedInstanceName,
    OrphanedCredential,
    enrol_connector,
)
from pm_ai.ports import (
    ArtifactBusy,
    CredentialProbePort,
    DaemonPort,
    DuplicateConnector,
    KeychainPort,
    KeychainUnavailable,
    KeyNotFound,
    ProbeFailed,
    ProbeUnreachable,
    UnknownConnectorSystem,
)

__all__ = [
    "EXIT_OK",
    "EXIT_REFUSAL",
    "EXIT_UNEXPECTED",
    "EXIT_UNHEALTHY",
    "EXIT_USAGE",
    "Command",
    "Context",
    "FirstRun",
    "GoalBook",
    "GoalOutcome",
    "HealthReport",
    "Refusal",
    "TABLE",
    "dispatch",
    "usage",
]

# ── The exit-code table ──────────────────────────────────────────────────────

EXIT_OK = 0
"""Everything the operator asked for happened."""

EXIT_UNEXPECTED = 1
"""A bug anywhere below. Distinct from 2 and 3 so a script can tell them apart."""

EXIT_USAGE = 2
"""The command line did not name something this CLI can run."""

EXIT_REFUSAL = 3
"""A stated, deliberate no — the daemon understood and declined.

`4c` declared this with no caller; `4j`'s three leaves are the first — an
already-enrolled key, an unreachable keychain, a `config.toml` the loader would
not act on. Declared here rather than there because the table is this module's
to own, and a slice that had to invent a code would invent a different one.
"""

EXIT_UNHEALTHY = 4
"""A probe ran and its answer was not `OK`. Not a failure of the probe itself.

Two commands produce it and both mean the same thing. `doctor` probes this
machine; `connector check` probes the providers the registered connectors talk
to. A connector that never answered inside CAP-35's bound is reported here — the
probe's own failure, per `8d` — and it lands on `4` rather than `1` because the
command did exactly what it was asked to.
"""


class Refusal(Exception):
    """A deliberate no, carrying the sentence the operator should read.

    Separate from an unexpected exception because the two need different
    responses: a refusal is the daemon working correctly and saying so, and
    printing a traceback over it would teach an operator to read every red
    message as a bug.
    """


@runtime_checkable
class HealthReport(Protocol):
    """What this module needs of `pm_ai.platform.doctor.Report`, and no more.

    Declared structurally because `surfaces` may not import `pm_ai.platform`:
    the probes reach `sqlite3`, `keyring` and `subprocess`, all three of which
    `.importlinter` forbids here *including through an intermediary*. So the
    probes are run by the composition root and the result arrives as a value.
    """

    @property
    def healthy(self) -> bool: ...

    def __str__(self) -> str: ...



@runtime_checkable
class OnboardOutcome(Protocol):
    """What `project add` produced, named structurally.

    `pm_ai.app.wiring.Onboarded` is the real type and lives above this package
    in the layer stack, so the CLI names the shape rather than the class — the
    same arrangement `HealthReport` is in, and for the same contract.
    """

    @property
    def project_id(self) -> str: ...
    @property
    def repository(self) -> Path: ...
    @property
    def gitignore(self) -> Path: ...
    @property
    def already_registered(self) -> bool: ...


@runtime_checkable
class FirstRun(Protocol):
    """What `pm-ai setup` needs beyond `onboard`, handed in by the composition root.

    A value rather than a daemon, because the machine `setup` exists for has
    none: composition stops at "no project enrolled" until the second step here
    has run. The keychain is this process's, the config reads and writes go
    through the single reader and writer (AD-5) — which `surfaces` may not
    reach, so `pm_ai.app.entry` binds them — and `diagnose` re-reads the machine
    rather than reporting what it looked like before setup changed it.
    """

    @property
    def keychain(self) -> KeychainPort: ...

    def read_config(self) -> bytes | None:
        """`config.toml`'s bytes, `None` when absent; `OSError` when unreadable."""
        ...

    def write_config(self, payload: bytes, *, expected: bytes | None) -> Path:
        """Replace `config.toml` with `payload`, refusing if it is not `expected`.

        `expected` is what `read_config` returned at the start of the run, so a
        file that changed underneath setup is refused (`ConfigRefused`) rather
        than written over.
        """
        ...

    def diagnose(self) -> HealthReport:
        """`1g`'s probes, run against the machine as it is now."""
        ...


@runtime_checkable
class GoalBook(Protocol):
    """What `pm-ai goal set` needs, handed in by the composition root (story 22b).

    `pm_ai.app.entry.GoalBook` is the real one. Named structurally because the
    read, the write and the event-log entry all go through storage, which
    `surfaces` may not reach (AD-30).
    """

    def read(self) -> GoalRegister:
        """`strategic_goals.md` as it is now; `MalformedGoals` if unreadable."""
        ...

    def set(self, goal: Goal) -> GoalOutcome:
        """Merge, write and record one goal; `MalformedGoals` leaves the file be."""
        ...


@runtime_checkable
class GoalOutcome(Protocol):
    """What `GoalBook.set` did — `pm_ai.app.entry.GoalWritten`, named by shape."""

    @property
    def path(self) -> Path | None: ...
    @property
    def revised(self) -> bool: ...
    @property
    def changed(self) -> bool: ...


def _no_onboarding(path: str, alias: str | None) -> OnboardOutcome:
    """The `Context` default: no onboarding sequence was injected.

    Refuses rather than returning a plausible-looking outcome, for
    `_no_dashboard`'s reason: `project add` creates directories and writes a
    registry, and a command that reported success without doing any of it would
    leave an operator believing their project was enrolled.
    """
    raise Refusal(
        f"no onboarding sequence was supplied to the CLI, so {path!r} was not "
        f"enrolled. This is a wiring fault in pm-ai, not something wrong with "
        f"the path."
    )


def _no_dashboard(scope: DataScope) -> Path:
    """The `Context` default: no pipeline was injected, so nothing may render.

    `23b`'s `run_dashboard` lives in `pm_ai.app`, which sits *above* this package
    in the enforced layer stack, so the CLI cannot reach it and has to be handed
    it — the same arrangement `probe_connectors` and `probe_credential` are in.
    The default refuses rather than returning a path, because a command that
    printed a filename without writing one is worse than a command that fails.
    """
    raise Refusal(
        f"no dashboard pipeline was supplied to the CLI, so the {scope} "
        f"dashboard cannot be rendered. That is a wiring fault in pm-ai rather "
        f"than anything about this machine's configuration."
    )


def _no_probe(system: str, credential: str) -> str:
    """The `Context` default: no probe was injected, so nothing may be enrolled."""
    raise UnknownConnectorSystem(
        f"no credential probe was supplied to the CLI, so {system!r} cannot be "
        f"checked. This is a wiring fault rather than anything about the "
        f"credential; enrolment refuses rather than sealing an unchecked secret."
    )


@dataclass(frozen=True, slots=True)
class Context:
    """Everything a subcommand may reach, handed in by the composition root."""

    daemon: DaemonPort | None
    """`None` when composition failed — an unenrolled project, an unwritable root,
    an unparseable `config.toml`. `doctor` runs anyway; that is its whole point."""

    diagnose: Callable[[], HealthReport]
    """Runs the startup probes. A callable rather than a report, so a command
    that never asks does not pay for `git --version` and a keychain round trip."""

    probe_connectors: Callable[[], Report]
    """Probes every registered connector, within CAP-35's bound.

    The same arrangement as `diagnose`, forced by the same contract: `8d`'s
    registry lives in `pm_ai.connectors`, and `.importlinter`'s
    `surfaces-through-core` forbids this package from importing it. So the
    composition root runs the probes and the report arrives as a value — which
    is also why `connector check` needs no daemon. The registry is a property of
    the *process*, and before composition it is empty, which is a first-run
    state rather than a refusal.

    Typed as `pm_ai.domain.health.Report` rather than structurally, unlike
    `HealthReport` above: that Protocol exists because `pm_ai.platform.doctor`
    is unreachable from here, whereas `pm_ai.domain` is the layer everything may
    name, and the leaf needs `probes` to tell an empty registry from a healthy
    one.
    """

    arguments: tuple[str, ...] = ()
    """The words after the subcommand, in the order `Command.takes` named them.

    Empty for every leaf that declares no arguments, which until story 8b was
    all of them: `dispatch` dropped everything after the leaf name, so
    `pm-ai connector add gitlab alpha` could not have been written. The arity is
    declared on the table rather than parsed by each handler, so a leaf cannot
    disagree with the usage line printed for it.
    """

    options: Mapping[str, str] = field(default_factory=dict)
    """The `--name value` pairs this invocation carried, keyed by option name.

    Only the options `Command.options` declares can be in here: `dispatch`
    refuses an unknown one with usage rather than passing it through, so a
    handler reading `options["scope"]` cannot be reading a word the table never
    promised. Absent means the operator did not say, which is a handler's cue to
    use its own default — never an empty string.
    """

    onboard: Callable[[str, str | None], OnboardOutcome] = _no_onboarding
    """Enrols a project: creates the directory, the rules and the registry entry.

    Injected for `dashboard`'s reason — the sequence reaches the filesystem, the
    single writer and `pm_ai.platform`'s id standard at once, and only
    `pm_ai.app` may do all three. Deliberately independent of `daemon`: this is
    the command that makes a daemon possible, so requiring one would make it
    unreachable on exactly the machine it exists for.
    """

    dashboard: Callable[[DataScope], Path] = _no_dashboard
    """Renders and writes one scope's `daily_dashboard.md`, returning its path.

    `23b`'s pipeline, injected for `probe_connectors`' reason: it reaches a
    connector, the scope model and the single writer at once, which only
    `pm_ai.app` may do. Refusals travel out as the exceptions `pm_ai.core` and
    `pm_ai.domain` already declare — a config with no display zone, a malformed
    goals file, a corrupt event-log segment, an undeclared scope — so the CLI
    maps them onto an exit code without naming anything above it.
    """

    probe_credential: CredentialProbePort = _no_probe
    """Asks a provider whether it accepts a credential (story 8b).

    A value for the same reason `probe_connectors` is one: the adapter lives in
    `pm_ai.connectors`, which this package may not import. Defaulted so every
    existing `Context(...)` in the suite keeps working, and the default refuses
    rather than passing — a probe that answered "fine" without asking would seal
    an unchecked credential, which is the failure 8b's whole ordering exists to
    prevent.
    """

    first_run: FirstRun | None = None
    """`pm-ai setup`'s keychain, config reader and writer, and fresh probes.

    `None` refuses `setup` as a wiring fault, for `_no_onboarding`'s reason: a
    setup that reported success without enrolling or writing anything would
    leave an operator believing the machine was ready.
    """

    goals: GoalBook | None = None
    """`strategic_goals.md`'s reader and writer. `None` refuses `goal set`."""

    unavailable: str | None = None
    """Why there is no daemon, in the composition root's own words.

    `None` when the daemon was built, or when whatever failed left no sentence
    worth repeating. Carried so a refusal can say *what* is missing: a
    `config.toml` the loader would not act on is the case this exists for —
    `4j`'s matrix requires `config show` to report the loader's own message, and
    a generic "could not build a daemon" would swallow the one sentence naming
    the offending key.
    """

    def require_daemon(self) -> DaemonPort:
        """The daemon, or a refusal naming what is missing.

        A refusal rather than a crash: a machine with no project enrolled is an
        incomplete setup, which pm-ai understands perfectly and declines to act
        on. `doctor` is the command that works regardless, and it does not call
        this.
        """
        if self.daemon is None:
            # The reason goes last and unedited. It is the composition root's
            # sentence — for a refused `config.toml`, the loader's own — and
            # anything appended to it (a full stop included) is this module
            # editing a message it did not write.
            explanation = f" The reason: {self.unavailable}" if self.unavailable else ""
            raise Refusal(
                "pm-ai could not build a daemon on this machine, so this command "
                "has nothing to run against. `pm-ai doctor` works regardless and "
                f"reports why.{explanation}"
            )
        return self.daemon


# ── The subcommand table ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Command:
    """One entry in the table: what it is for, and what it runs.

    A `run` of `None` makes this a group — a name that exists so its leaves have
    somewhere to hang, and which does nothing on its own.
    """

    summary: str
    run: Callable[[Context], int] | None = None
    leaves: Mapping[str, "Command"] = field(default_factory=dict)
    takes: tuple[str, ...] = ()
    """The positional arguments this command requires, named for its usage line.

    Declared rather than inferred so the refusal and the usage text cannot
    disagree, and so a leaf that takes none keeps refusing trailing words — the
    behaviour 4j added, which silently dropping `rest` would have undone.
    """

    optional: tuple[str, ...] = ()
    """Positionals this command accepts but does not require, after `takes`.

    Added by story `4k`, whose `project add <path> [alias]` is the first command
    with one. Neither existing mechanism could express it: `takes` is exact
    arity — `len(supplied) != len(leaf.takes)` refuses — and `options` is
    `--name value`, which is a different thing to type and a different thing to
    read. The frozen matrix specifies a bare positional, and the alias is the
    id: `pm-ai project add ~/dev/"My Project" payments`.

    Declared on the table for `takes`' reason, and ordered: a handler reads
    `context.arguments` positionally, so a missing optional is an absent tail
    rather than a hole in the middle. There is one today and the arity check
    below is written for any number.
    """

    options: tuple[str, ...] = ()
    """The long options this command accepts, each `--name <value>` and optional.

    The mechanism `23b` added, because `dashboard` is the first command to take
    an argument it can also be run without. `takes` above could not express it:
    it is positional and required, and the top-level branch in `dispatch`
    refused *every* trailing word for a command with a `run`.

    Declared on the table for `takes`' reason — one place decides what a command
    accepts, so the refusal and the usage line cannot disagree — and closed, so
    an option nobody declared is a usage error rather than a word that vanishes.
    Every one of them takes a value; there are no flags here, because a flag
    that is absent and a flag that is false are the same word on a command line
    and no command in this table needs to tell them apart.
    """


def _doctor(context: Context) -> int:
    """Story 1g's probes, reachable at last.

    Every state `Health` declares maps to an exit code here, through
    `Report.healthy` — which is `OK` and nothing else. `ABSENT` in particular is
    not success: it means setup is incomplete and encrypted writes will be
    refused, and a `doctor` that exited 0 over it would be the summary an
    operator trusts while the morning briefing cannot decrypt anything.
    """
    report = context.diagnose()
    print(report)
    return EXIT_OK if report.healthy else EXIT_UNHEALTHY


def _key_enrol(context: Context) -> int:
    """Story 4b's `enrol`, invoked with the daemon's own keychain.

    Three outcomes, and only one of them is this module's to decide. `4b` mints
    and stores; the refusals it raises already carry the sentence an operator
    needs, so they are passed through *verbatim* and mapped onto `EXIT_REFUSAL`.
    Rewriting them here would put the consequence of minting a second key — every
    sealed artifact permanently unreadable — behind a paraphrase.

    The three failure modes stay apart because `pm_ai.ports` keeps them apart:
    `KeyAlreadyEnrolled` is the keychain answering "something is there",
    `KeychainBackendMissing` is no keychain library to ask, and
    `KeychainUnavailable` is a keychain that could not be reached. All three are
    a refusal rather than a crash, and each says a different thing.

    Nothing here ever holds key material. `enrol` returns the *name* it stored
    under, deliberately, so there is no secret in this frame to print by
    accident.
    """
    keychain = context.require_daemon().keychain
    try:
        name = enrol(keychain)
    except KeyAlreadyEnrolled as present:
        raise Refusal(str(present)) from present
    except KeychainUnavailable as unreachable:
        # Catches `KeychainBackendMissing` too, which is a subclass — the
        # distinction that matters is in the message, and the message is the
        # adapter's. Collapsing them into one sentence here would send an
        # operator to unlock a keychain that is not installed.
        raise Refusal(str(unreachable)) from unreachable
    print(
        f"a master key is enrolled under {name!r}. It stays in the keychain: "
        f"pm-ai fetches it when it seals an artifact, and no command prints it."
    )
    return EXIT_OK


def _config_show(context: Context) -> int:
    """Every setting `config.toml` carries, each marked set or default.

    The mark is the point. A value the operator wrote and a value they inherited
    look identical in a dump, and the difference is what decides whether they
    think the file is doing anything — the failure being an operator who edits a
    key that never took effect and reads the unchanged output as confirmation.

    "Default" is decided by comparing against `Config()`'s own field defaults,
    which is the only definition of the word that cannot drift from the loader.
    It is deliberately a comparison of values rather than a record of what the
    file contained: `pm_ai.core.config` parses bytes and returns a `Config`,
    keeping no provenance, and inventing a second parse here to recover it would
    be exactly the reimplementation this slice may not do. The one case it reads
    conservatively is `verbose_logging = false` written out longhand — a
    setting that agrees with its default, reported as a default. Neither the
    rate nor the handle can reach that state: `config.toml` refuses both unset
    values outright, so writing one is an error rather than a mislabelled row.

    No file is opened. The daemon already holds the interpreted `Config`,
    because the composition root read it through the single reader (AD-5), and
    a `config.toml` that would not parse is why there is no daemon at all —
    which `require_daemon` reports with the loader's own refusal inside it.
    """
    settings = context.require_daemon().config
    defaults = Config()
    names = [field_.name for field_ in fields(Config)]
    width = max(len(name) for name in names)
    for name in names:
        value = getattr(settings, name)
        origin = "default" if value == getattr(defaults, name) else "set"
        print(f"{name:<{width}}  {value!r:<8}  ({origin})")
    print()
    print(
        "(default) means the value equals pm-ai's built-in default, and (set) "
        "means it differs. `Config` keeps no provenance, so this compares "
        "values, not origins: a key written into config.toml with the same "
        "value the default already has reads as (default)."
    )
    return EXIT_OK



def _connector_add(context: Context) -> int:
    """`pm-ai connector add <system> <instance>` — story 8b's surface.

    The credential is prompted for, never taken as an argument: an argument is
    in the process table while it runs and in shell history afterwards, which
    are two places a token outlives the command that used it.

    Nothing here decides anything. The order — probe, seal, configure — and
    every refusal belong to `pm_ai.core.connector_enrolment`; this reads two
    words and a secret, hands them over, and maps what comes back onto 4c's
    table. The probe arrives as a value for the same reason `connector check`'s
    does: `surfaces-through-core` forbids this package from importing
    `pm_ai.connectors`.
    """
    daemon = context.require_daemon()
    system, instance = context.arguments

    if not sys.stdin.isatty():
        # `getpass` falls back to reading an echoing stdin when there is no
        # terminal, so a piped or cron-driven run would put the credential in
        # shell history and in the terminal scrollback. Refusing is the only
        # answer that keeps the promise the prompt makes.
        raise Refusal(
            "a credential can only be typed at a terminal. stdin is not a TTY "
            "here — this is a pipe, a cron job or a CI step — and prompting "
            "would echo the secret and leave it in history. Run "
            "`pm-ai connector add` from an interactive shell."
        )

    credential = getpass.getpass(f"{system} credential for {instance}: ")
    if not credential.strip():
        raise Refusal("no credential was typed, so nothing was enrolled.")

    try:
        answer = enrol_connector(
            daemon.storage,
            system=system,
            instance=instance,
            credential=credential,
            probe=context.probe_credential,
        )
    except (DuplicateConnector, MalformedInstanceName) as refused:
        raise Refusal(str(refused)) from refused
    except UnknownConnectorSystem as unknown:
        raise Refusal(str(unknown)) from unknown
    except ProbeUnreachable as silent:
        # Named before its base class, so "the provider never answered" does not
        # read as "your token is wrong" — the operator would reissue a perfectly
        # good credential.
        raise Refusal(
            f"{silent} This is the network or the provider, not the credential."
        ) from silent
    except ProbeFailed as rejected:
        raise Refusal(str(rejected)) from rejected
    except KeyNotFound as keyless:
        # The most common first-run state, and it reached the operator as a
        # traceback: enrolment reads the sealed store for its duplicate check,
        # which needs the master key.
        raise Refusal(
            f"no master key is enrolled on this machine, so a credential cannot "
            f"be sealed. Run `pm-ai key enrol` first, then enrol the connector. "
            f"({keyless})"
        ) from keyless
    except KeychainUnavailable as unreachable:
        raise Refusal(str(unreachable)) from unreachable
    except ArtifactBusy as claimed:
        raise Refusal(str(claimed)) from claimed
    except OrphanedCredential as orphaned:
        # Not a refusal that left nothing behind — the one case where something
        # *was* written. It exits 3 like any other stated no, and says what is
        # on the machine, because a credential nothing refers to is only
        # findable if this sentence is printed.
        raise Refusal(str(orphaned)) from orphaned

    print(answer)
    print(
        f"{instance} is enrolled. It becomes active at the next start — "
        f"connectors are registered when the daemon is composed, so nothing is "
        f"harvesting from it yet."
    )
    return EXIT_OK

def _connector_check(context: Context) -> int:
    """CAP-35's live probe: every registered connector, bounded at ten seconds.

    The bound belongs to `8d`'s registry, which starts the probes together and
    abandons whichever has not answered by the deadline — a blocking read cannot
    cancel itself, so what is bounded is the *waiting*. Nothing here enforces it
    and nothing here probes: this prints what came back.

    Every connector is reported, always, including the siblings of one that
    failed. That is the whole reason `8d`'s probes report rather than raise, and
    a surface that stopped at the first bad row would give the rule away at the
    only place a human reads it.

    An empty registry exits `0` and says so. Nothing is registered before
    composition, and a machine with no connectors is a first run rather than a
    broken one — there is no claim of reachability to be false. A connector that
    *is* registered and answers `ABSENT` is a different state and exits `4`:
    setup is incomplete, harvests are being skipped, and `Health.ABSENT` is
    expressly not a pass.
    """
    report = context.probe_connectors()
    if not report.probes:
        print(
            "no connectors are registered, so there is nothing to probe. That is "
            "an ordinary first-run state: connectors are registered when the "
            "daemon is composed."
        )
        return EXIT_OK
    for probe in report.probes:
        print(probe)
    print()
    # Not `print(report)`: `Report.__str__` ends in "pm-ai is healthy.", which is
    # a claim about the machine. This command asked about providers, and the two
    # verdicts are not interchangeable — `doctor` can pass while every connector
    # is refused.
    verdict = (
        "every connector answered."
        if report.healthy
        else "not every connector is healthy."
    )
    print(f"{len(report.probes)} connectors probed; {verdict}")
    return EXIT_OK if report.healthy else EXIT_UNHEALTHY


SCOPE_ARGUMENT = "scope"
"""The one option in the table, named once so the handler and the row agree."""

_SCOPE_KINDS: Mapping[str, ScopeKind] = {kind.value: kind for kind in ScopeKind}
"""Every scope word `--scope` can *parse*, which is deliberately more than it
can *act on*.

`people:bob` and `application` parse here and are refused a line later by the
pipeline, naming the two trees that declare a dashboard. The two answers are
different facts and get different exit codes: `alpha` is a command line pm-ai
cannot read (usage, `2`), and `people:bob` is one it read and declined (`3`).
Collapsing them would tell an operator who named a real scope that they had
mistyped.
"""


def _scope(text: str | None) -> DataScope | None:
    """`--scope`'s word as a scope, or `None` when it is not one.

    `None` in, personal out: CAP-9 names `~/.manager-ai/memory/daily_dashboard.md`
    by path, so the PM's own is what the command renders when nobody said.

    `None` out is the unparseable case and never a refusal — `project:` with no
    id and `alpha` with no kind are both command lines this CLI cannot read, and
    `DataScope`'s own constructor is what decides the rest (a project id is
    required, a personal scope may not carry one).
    """
    if text is None:
        return DataScope(ScopeKind.PERSONAL)
    word, separator, subject = text.partition(":")
    kind = _SCOPE_KINDS.get(word)
    if kind is None:
        return None
    if kind is ScopeKind.PROJECT:
        return DataScope(kind, project_id=subject) if subject else None
    if kind is ScopeKind.PEOPLE:
        return DataScope(kind, person_id=subject) if subject else None
    # `personal:anything` is not a scope with a subject — it is a word with a
    # colon in it, and reading it as bare `personal` would silently discard
    # whatever the operator thought they were naming.
    return None if separator else DataScope(kind)


def _dashboard(context: Context) -> int:
    """Story 23b's render, run once and exited — no scheduler, ever (AD-7).

    Three outcomes, and the split is the whole reason this handler exists rather
    than a bare call. An unreadable `--scope` is a usage error, because nothing
    was asked of the daemon. Everything the pipeline declines — an unset display
    zone, a goals file with a duplicate id, a corrupt event-log segment, a scope
    whose tree declares no dashboard — is a refusal carrying its own sentence,
    passed through **verbatim**: those messages name the offending key, id or
    segment, and a paraphrase here would drop the one detail that makes them
    actionable.

    A calendar that could not be read is **neither**. It exits zero with the file
    written, because a day whose calendar went dark is a dashboard that says so —
    not a failed command. That decision lives in the renderer, and this handler's
    part in it is not to have an `except` for it.
    """
    requested = context.options.get(SCOPE_ARGUMENT)
    scope = _scope(requested)
    if scope is None:
        print(
            f"pm-ai: `--{SCOPE_ARGUMENT} {requested}` is not a scope. Write "
            f"`personal`, or `project:<id>` naming an enrolled project.\n",
            file=sys.stderr,
        )
        print(usage(), file=sys.stderr)
        return EXIT_USAGE
    context.require_daemon()
    try:
        written = context.dashboard(scope)
    except (
        ConfigRefused,
        MalformedGoals,
        MalformedEntry,
        UnknownCategory,
        ScopeResolutionError,
    ) as refused:
        raise Refusal(str(refused)) from refused
    print(f"the {scope} dashboard is written: {written}")
    return EXIT_OK


def _project_add(context: Context) -> int:
    """`pm-ai project add <path> [alias]` — story 4k's surface.

    Nothing here decides anything. The order — validate, claim, create, rule,
    structure, register — and every refusal belong to
    `pm_ai.app.wiring.onboard_project`; this reads one or two words and maps
    what comes back onto `4c`'s table.

    Needs no daemon, which is the point: on the machine this command exists for
    there is no enrolled project, so composition has already stopped and
    `require_daemon()` would refuse the one command that would fix it.

    An already-onboarded project exits `0` and says so. It is an ordinary
    outcome — the operator ran the command twice, or a colleague scripted it —
    and a refusal would make `project add` unsafe to put in a setup script.
    """
    path, alias = (context.arguments + (None,))[:2]  # type: ignore[operator]
    try:
        outcome = context.onboard(str(path), None if alias is None else str(alias))
    except (ScopeResolutionError, RegistryRefused, ClaimHeld) as refused:
        raise Refusal(str(refused)) from refused
    if outcome.already_registered:
        print(
            f"{outcome.project_id} is already onboarded at {outcome.repository}; "
            f"nothing changed."
        )
        return EXIT_OK
    print(f"{outcome.project_id} is onboarded at {outcome.repository}")
    print(f"git exclusion rules are in {outcome.gitignore}")
    return EXIT_OK


# ── `pm-ai setup` (story 4h) ─────────────────────────────────────────────────

SETUP_ATTEMPTS = 3
"""How many answers one question gets before setup refuses.

Bounded because an unbounded re-prompt is a loop an operator can only leave
with Ctrl-C, and a mandatory question with no way out but an interrupt reads
as a hang. Three is enough to fix a typo twice.
"""


class _Inadmissible(Exception):
    """An answer setup cannot use, carrying the sentence that says why.

    Local and private: it never leaves `_answer`, which turns it into a
    re-prompt, and after the last attempt into a `Refusal`.
    """


def _ask(prompt: str) -> str:
    """One line from the operator, or a refusal when they stop answering.

    `EOFError` is Ctrl-D, or a terminal that went away, and `KeyboardInterrupt`
    is Ctrl-C. Both are an interruption rather than an answer, and the steps
    before it stay done — which is what makes re-running `setup` continue from
    where this one stopped. Ctrl-C is caught here rather than left to
    `entry.main`'s generic "interrupted", which cannot say what stayed done.
    """
    try:
        return input(prompt)
    except UnicodeDecodeError:
        # Bytes the terminal sent that are not valid in its own encoding. An
        # answer that cannot be read, so it is asked again — never a traceback
        # after the key and the project are already done.
        raise _Inadmissible(
            "that answer was not valid text in this terminal's encoding."
        ) from None
    except (EOFError, KeyboardInterrupt) as closed:
        raise Refusal(
            "setup was interrupted at a prompt. Every step it reported done "
            "stays done; run `pm-ai setup` again to continue from here."
        ) from closed


def _answer[T](
    question: str, interpret: Callable[[str], T], *, step: str, default: str | None = None
) -> T:
    """Ask until `interpret` accepts an answer, at most `SETUP_ATTEMPTS` times.

    `interpret` raises `_Inadmissible` or `ConfigRefused` for an answer it will
    not take, and both are printed and asked again. `ConfigRefused` is the one
    that matters: `Config.__post_init__` is where a whitespace handle or a
    negative rate is refused, and letting it escape here would be a traceback
    after the key and the project are already done.
    """
    prompt = f"{question} [{default}]: " if default else f"{question}: "
    for _ in range(SETUP_ATTEMPTS):
        try:
            return interpret(_ask(prompt))
        except (_Inadmissible, ConfigRefused) as refused:
            print(f"  {refused}", file=sys.stderr)
    raise Refusal(
        f"setup stopped at {step}: no admissible answer to {question!r} after "
        f"{SETUP_ATTEMPTS} attempts. Every earlier step stays done; run "
        f"`pm-ai setup` again to continue from here."
    )


def _setup_key(first_run: FirstRun) -> None:
    """Step 1: the master key — `4b`'s `enrol`, and an enrolled key is done.

    First because every encrypted write refuses without it. `KeyAlreadyEnrolled`
    is a completed step here and not the refusal `pm-ai key enrol` makes of it:
    the operator asked for a ready machine, not for a new key, and one is
    there. Nothing replaces it — `enrol` never does.
    """
    print("[1/3] master key — first, because every encrypted write refuses without it")
    try:
        name = enrol(first_run.keychain)
    except KeyAlreadyEnrolled:
        print("      already enrolled; the keychain was not written.")
        return
    except KeychainUnavailable as unreachable:
        raise Refusal(
            f"setup stopped at step 1 (master key) and nothing after it ran: "
            f"{unreachable}"
        ) from unreachable
    print(f"      a master key is enrolled under {name!r}; no command prints it.")


def _project_answer(raw: str) -> str:
    path = raw.strip()
    if not path:
        raise _Inadmissible(
            "a project path is required: pm-ai acts only on projects it was "
            "given, and none can be resolved without one."
        )
    return path


def _setup_project(context: Context) -> None:
    """Step 2: one project — `4k`'s `onboard`, and a registered one is done.

    Second because nothing resolves a project path until the registry names
    one. A changed path for an id already registered is `4d`'s refusal and
    ends setup here: relocating a tree is a migration, not a setup step.
    """
    print("[2/3] project — second, because no path resolves until one is registered")
    path = _answer("project directory", _project_answer, step="step 2 (project)")
    alias = _answer(
        "project id (empty to use the directory's name)",
        lambda raw: raw.strip() or None,
        step="step 2 (project)",
    )
    try:
        outcome = context.onboard(path, alias)
    except (ScopeResolutionError, RegistryRefused, ClaimHeld) as refused:
        raise Refusal(
            f"setup stopped at step 2 (project); step 1 stays done: {refused}"
        ) from refused
    except OSError as unwritable:
        raise Refusal(
            f"setup stopped at step 2 (project); step 1 stays done. The "
            f"registry could not be written: {unwritable}"
        ) from unwritable
    if outcome.already_registered:
        print(
            f"      {outcome.project_id} is already registered at "
            f"{outcome.repository}; nothing changed."
        )
    else:
        print(f"      {outcome.project_id} is registered at {outcome.repository}")


def _handle(current: Config) -> Callable[[str], Config]:
    def interpret(raw: str) -> Config:
        # Stripped unless that leaves nothing: an all-whitespace answer reaches
        # `Config` as typed, so its own refusal names it, rather than being
        # folded into the empty answer and reported as missing.
        handle = raw.strip() or raw
        if not handle:
            raise _Inadmissible(
                "pm_handle is required. An unset handle matches no speaker, so "
                "nothing spoken to pm-ai would ever be attributed to the PM."
            )
        return replace(current, pm_handle=handle)

    return interpret


def _rate(current: Config) -> Callable[[str], Config]:
    def interpret(raw: str) -> Config:
        text = raw.strip()
        if not text:
            return current
        try:
            rate = float(text)
        except ValueError:
            raise _Inadmissible(
                f"{text!r} is not a number. blended_hourly_rate is a cost per "
                f"attendee-hour; leave it empty to keep it unset."
            ) from None
        return replace(current, blended_hourly_rate=rate)

    return interpret


def _zone(current: Config) -> Callable[[str], Config]:
    def interpret(raw: str) -> Config:
        text = raw.strip()
        return current if not text else replace(current, display_timezone=text)

    return interpret


_YES = frozenset({"y", "yes"})
_NO = frozenset({"n", "no"})


def _verbose(current: Config) -> Callable[[str], Config]:
    def interpret(raw: str) -> Config:
        text = raw.strip().lower()
        if not text:
            return current
        if text in _YES or text in _NO:
            return replace(current, verbose_logging=text in _YES)
        raise _Inadmissible(f"{raw.strip()!r} is not y or n.")

    return interpret


def _empty_means(has_default: bool) -> str:
    """What an empty answer does, which depends on whether a value exists."""
    return "empty to keep it" if has_default else "empty to leave unset"


def _setup_config(first_run: FirstRun, existing: bytes | None, current: Config) -> None:
    """Step 3: `config.toml` — asked for, rendered by `4g`, written once.

    Last because it is the only step whose absence is survivable: a machine
    with no handle runs and attributes nothing to the PM. A config that already
    parses and names a handle is a completed step, as an enrolled key is, and
    nothing is asked or written. Otherwise every question is `4a`'s closed
    vocabulary and nothing else; each answer is a `Config` before it is
    accepted, so `__post_init__` refuses it at the prompt that asked.
    """
    print("[3/3] config.toml — last, because it is the only step a machine survives without")
    if current.pm_handle:
        print(f"      already configured (pm_handle is {current.pm_handle}); nothing was written.")
        return
    step = "step 3 (config.toml)"
    settled = _answer("pm_handle (who is the PM)", _handle(current), step=step)
    # `0.0` is the rate's unset state, not a value: `config.toml` refuses an
    # explicit zero, so a rate that reads back as `0.0` was never set, and
    # offering it as a default to keep would describe a setting nobody chose.
    rate = settled.blended_hourly_rate
    settled = _answer(
        f"blended_hourly_rate ({_empty_means(bool(rate))})",
        _rate(settled),
        step=step,
        default=repr(rate) if rate else None,
    )
    zone = settled.display_timezone
    settled = _answer(
        f"display_timezone, e.g. Europe/Warsaw ({_empty_means(bool(zone))})",
        _zone(settled),
        step=step,
        default=zone or None,
    )
    settled = _answer(
        "verbose_logging (y/n)",
        _verbose(settled),
        step=step,
        default="y" if settled.verbose_logging else "n",
    )
    try:
        written = first_run.write_config(render_config(settled), expected=existing)
    except ConfigRefused as refused:
        raise Refusal(
            f"setup stopped at {step}; steps 1 and 2 stay done: {refused}"
        ) from refused
    except (OSError, ArtifactBusy) as unwritable:
        raise Refusal(
            f"setup stopped at {step}; steps 1 and 2 stay done. config.toml "
            f"could not be written: {unwritable}"
        ) from unwritable
    print(f"      written to {written}")


def _setup(context: Context) -> int:
    """`pm-ai setup` — key, project, config, then `1g`'s probes (story 4h).

    Nothing here is reimplemented: `4b` enrols, `4k` registers, `4g` renders.
    This owns the order, the prompts, and the claim at the end — which is the
    probe report and not "the steps succeeded", because every step can succeed
    on a machine that is still not ready.

    Two checks precede every write, keychain included. The TTY check, because a
    key minted before a non-interactive refusal is a write nobody asked for.
    And the read of any existing `config.toml`, because a file 4a refuses has
    to be refused before anything else happens, and left exactly as it is.
    """
    # `sys.stdin` is `None` in a detached process — no descriptor 0 at all —
    # which is the same fact as a pipe for this purpose, and must not reach
    # `main` as an `AttributeError` and exit 1.
    if sys.stdin is None or not sys.stdin.isatty():
        raise Refusal(
            "setup asks questions and can only run at a terminal. stdin is not a "
            "TTY here — a pipe, a cron job or a CI step — so nothing was done: "
            "no key was enrolled and no file was written. Run `pm-ai setup` "
            "from an interactive shell."
        )
    first_run = context.first_run
    if first_run is None:
        raise Refusal(
            "no setup sequence was supplied to the CLI, so nothing was done. "
            "This is a wiring fault in pm-ai, not something wrong with this "
            "machine."
        )
    try:
        existing = first_run.read_config()
    except OSError as unreadable:
        raise Refusal(
            f"config.toml exists and could not be read, so setup did nothing: "
            f"{unreadable}. It is not written over — fix its permissions and "
            f"run `pm-ai setup` again."
        ) from unreadable
    try:
        current = load_config(existing)
    except ConfigRefused as refused:
        raise Refusal(
            f"config.toml says something pm-ai will not act on, so setup did "
            f"nothing and left the file as it is: {refused}"
        ) from refused

    _setup_key(first_run)
    _setup_project(context)
    _setup_config(first_run, existing, current)

    print()
    report = first_run.diagnose()
    print(report)
    return EXIT_OK if report.healthy else EXIT_UNHEALTHY


# ── `pm-ai goal set` (story 22b) ─────────────────────────────────────────────


def _goal_prompt(question: str, default: str | None) -> str:
    """One answer, the default for an empty one, or a refusal if input stops."""
    prompt = f"{question} [{default}]: " if default else f"{question}: "
    try:
        answer = input(prompt)
    except UnicodeDecodeError:
        raise Refusal(
            "that answer was not valid text in this terminal's encoding, so "
            "nothing was written."
        ) from None
    except (EOFError, KeyboardInterrupt) as closed:
        raise Refusal(
            "goal set was interrupted at a prompt, so nothing was written."
        ) from closed
    return answer if answer.strip() or default is None else default


def _goal_set(context: Context) -> int:
    """`pm-ai goal set` — ask for one goal, then hand it to `22b`'s writer.

    Four questions: the id, then the domain, horizon and title, each offering
    the goal's current value when the id is already set, so a revision is four
    presses of Enter and the one word that changed. Nothing is decided here —
    the closed vocabularies and the id charset are `declare_goal`'s, the merge
    is `set_goal`'s — and every refusal is passed through verbatim.

    The file is read before the first prompt, so a `strategic_goals.md` the
    parser refuses is refused before anything is asked, and is not written.
    """
    if sys.stdin is None or not sys.stdin.isatty():
        raise Refusal(
            "goal set asks questions and can only run at a terminal. stdin is "
            "not a TTY here — a pipe, a cron job or a CI step — so nothing was "
            "asked and nothing was written."
        )
    context.require_daemon()
    goals = context.goals
    if goals is None:
        raise Refusal(
            "no goal writer was supplied to the CLI, so nothing was written. "
            "This is a wiring fault in pm-ai, not something wrong with the goal."
        )
    try:
        register = goals.read()
        goal_id = _goal_prompt("goal id, e.g. g_payments_latency", None).strip()
        current = register.get(goal_id)
        domain = _goal_prompt(
            "domain (project, team or personal)",
            None if current is None else current.domain.value,
        )
        horizon = _goal_prompt(
            "horizon (short, medium or long)",
            None if current is None else current.horizon.value,
        )
        title = _goal_prompt("title", None if current is None else current.title)
        goal = declare_goal(
            goal_id=goal_id,
            domain=domain,
            horizon=horizon,
            title=title,
            scope=GOALS_SCOPE,
        )
        outcome = goals.set(goal)
    except (MalformedGoals, GoalUnrecorded) as refused:
        # `GoalUnrecorded` is the one refusal after which the file *has*
        # changed, and its own sentence says so; it is passed on unedited.
        raise Refusal(str(refused)) from refused
    except (OSError, ArtifactBusy) as unwritable:
        raise Refusal(
            f"strategic_goals.md could not be written, so nothing changed: "
            f"{unwritable}"
        ) from unwritable
    if not outcome.changed:
        print(f"{goal.goal_id} is unchanged; nothing was written or recorded.")
        return EXIT_OK
    # The verb from the read `set` merged onto, not from `register` above: the
    # file may have gained this id while the prompts were open.
    verb = "revised" if outcome.revised else "set"
    print(f"{goal.goal_id} is {verb} in {outcome.path}")
    return EXIT_OK


TABLE: Mapping[str, Command] = {
    "dashboard": Command(
        "render this scope's daily_dashboard.md, once",
        _dashboard,
        options=(SCOPE_ARGUMENT,),
    ),
    "doctor": Command("check this machine and report what is wrong with it", _doctor),
    # `4h`: the one command that sequences the three below it on a new machine.
    "setup": Command(
        "first run: enrol the key, register a project, write config.toml, then probe",
        _setup,
    ),
    # The three groups, each with the one leaf `4j` hung on it. They were in the
    # table from `4c` with no leaves, so the shape of the CLI was settled in one
    # place and this slice added mappings rather than inventing a second table.
    "key": Command(
        "manage the master key pm-ai seals artifacts with",
        leaves={
            "enrol": Command("mint the master key and store it in the keychain", _key_enrol),
        },
    ),
    "config": Command(
        "inspect ~/.pm-ai/config.toml",
        leaves={
            "show": Command("print every setting, marked set or default", _config_show),
        },
    ),
    "project": Command(
        "the projects pm-ai acts on (AD-11: registered, never discovered)",
        leaves={
            "add": Command(
                "enrol a project: create its tree, its git rules and its entry",
                _project_add,
                takes=("path",),
                optional=("alias",),
            ),
        },
    ),
    "goal": Command(
        "the strategic goals every recommendation is aligned to",
        leaves={
            "set": Command("create or revise one goal in strategic_goals.md", _goal_set),
        },
    ),
    "connector": Command(
        "the connectors this daemon harvests from",
        leaves={
            "add": Command(
                "enrol a connector: probe the credential, then seal it",
                _connector_add,
                takes=("system", "instance"),
            ),
            "check": Command("probe every connector, within 10s in total", _connector_check),
        },
    ),
}

_HELP_FLAGS = frozenset({"-h", "--help", "help"})


def _spelled(name: str, command: Command) -> str:
    """One command's name with what it takes, as an operator would type it.

    Required positionals in angle brackets and optional options in square ones,
    which is the convention every other CLI on the machine uses. Composed from
    `takes` and `options` rather than written out beside the summary, so a row
    that gains an argument cannot keep an old usage line.
    """
    required = "".join(f" <{argument}>" for argument in command.takes)
    positional = "".join(f" [{argument}]" for argument in command.optional)
    optional = "".join(f" [--{option} <{option}>]" for option in command.options)
    return f"{name}{required}{positional}{optional}"


def usage(*, group: str | None = None) -> str:
    """The whole table, or one group's leaves.

    A group with no leaves says so rather than printing an empty list: "not
    implemented yet" and "you typed the wrong leaf name" are different mistakes.
    """
    if group is None:
        lines = ["usage: pm-ai <command> [<subcommand>]", "", "commands:"]
        spelled = {name: _spelled(name, command) for name, command in TABLE.items()}
        width = max(len(text) for text in spelled.values())
        lines += [
            f"  {spelled[name]:<{width}}  {command.summary}"
            for name, command in TABLE.items()
        ]
        return "\n".join(lines)
    command = TABLE.get(group)
    if command is None:
        # Exported, so a caller that does not already know `group` names a real
        # command gets the top-level usage rather than a KeyError surfacing as
        # an unexplained exit 1.
        return usage()
    lines = [f"usage: pm-ai {group} <subcommand>", "", f"{group}: {command.summary}", ""]
    if not command.leaves:
        lines.append(f"  no `pm-ai {group}` subcommand is implemented yet.")
    else:
        spelled = {
            name: _spelled(name, leaf) for name, leaf in command.leaves.items()
        }
        width = max(len(text) for text in spelled.values())
        lines += [
            f"  {spelled[name]:<{width}}  {leaf.summary}"
            for name, leaf in command.leaves.items()
        ]
    return "\n".join(lines)


def dispatch(
    argv: Sequence[str],
    *,
    daemon: DaemonPort | None,
    diagnose: Callable[[], HealthReport],
    probe_connectors: Callable[[], Report],
    probe_credential: CredentialProbePort = _no_probe,
    onboard: Callable[[str, str | None], OnboardOutcome] = _no_onboarding,
    dashboard: Callable[[DataScope], Path] = _no_dashboard,
    first_run: FirstRun | None = None,
    goals: GoalBook | None = None,
    unavailable: str | None = None,
) -> int:
    """Run what `argv` names, and return the exit code the table gives it.

    `argv` is the argument vector *without* the program name, and it is passed
    rather than read from `sys.argv`, so every row of this story's matrix is a
    unit test and none needs a subprocess.

    Hand-rolled rather than `argparse`: the surface is a table of at most two
    words, and `argparse` answers `--help` by raising `SystemExit` from inside
    the parse — which is exactly the control flow an explicitly-passed `argv`
    exists to avoid. (`pm_ai.app.entry` catches `SystemExit` anyway, because a
    library below here may still raise one.)
    """
    context = Context(
        daemon=daemon,
        diagnose=diagnose,
        probe_connectors=probe_connectors,
        probe_credential=probe_credential,
        onboard=onboard,
        dashboard=dashboard,
        first_run=first_run,
        goals=goals,
        unavailable=unavailable,
    )
    if not argv:
        # A bare `pm-ai` will open a REPL (CAP-18) and that is `4e`. Until then
        # it must not exit 0: a bare call that silently succeeded is how a broken
        # install reads as a working one.
        print(usage(), file=sys.stderr)
        return EXIT_USAGE
    name, *rest = argv
    if name in _HELP_FLAGS:
        if rest:
            # Every other path refuses trailing words; this one dropped them and
            # exited 0, so `pm-ai --help enrol` looked like it had answered a
            # question about `enrol` while printing the top-level usage.
            print(f"pm-ai: `{name}` takes no arguments\n", file=sys.stderr)
            print(usage(), file=sys.stderr)
            return EXIT_USAGE
        print(usage())
        return EXIT_OK
    command = TABLE.get(name)
    if command is None:
        print(f"pm-ai: unknown command {name!r}\n", file=sys.stderr)
        print(usage(), file=sys.stderr)
        return EXIT_USAGE
    if command.run is not None and command.leaves:
        # Measured: the `run` branch below wins and every leaf is unreachable,
        # so `pm-ai demo sub` answers "takes no arguments" instead of running
        # `sub`. Harmless while no command has both, and a silent trap the
        # moment `4k` or `8b` hangs a leaf on a group that also acts alone.
        raise ValueError(
            f"the {name!r} command declares both a `run` and leaves "
            f"({sorted(command.leaves)}). One or the other: a command with "
            f"both shadows every leaf it has."
        )
    if command.run is not None:
        if rest and not command.options:
            print(f"pm-ai: `{name}` takes no arguments\n", file=sys.stderr)
            print(usage(), file=sys.stderr)
            return EXIT_USAGE
        # `23b`'s mechanism, and it narrows rather than relaxes the rule above:
        # a command declaring no options still refuses every trailing word, and
        # one that declares some refuses every word that is not one of them. The
        # flag an operator invented to be careful with must never be the thing
        # that silently vanishes.
        named, misread = _options(rest, allowed=command.options)
        if misread is not None:
            print(f"pm-ai: `{name}` {misread}\n", file=sys.stderr)
            print(usage(), file=sys.stderr)
            return EXIT_USAGE
        return _run(command.run, replace(context, options=named))
    leaf = command.leaves.get(rest[0]) if rest else None
    if leaf is None or leaf.run is None:
        print(usage(group=name), file=sys.stderr)
        return EXIT_USAGE
    supplied = tuple(rest[1:])
    if not len(leaf.takes) <= len(supplied) <= len(leaf.takes) + len(leaf.optional):
        # 4j refused every trailing word because no leaf took one. 8b's
        # `connector add` does, so the refusal became about *arity* — still a
        # refusal, never a silent drop: the flag an operator invented to be
        # careful with must not be the thing that vanishes. 4k made it a range
        # rather than an equality, because `project add` takes an optional alias;
        # too many words still refuse, which is what keeps a typo'd third
        # argument from being swallowed.
        expected = (
            " ".join(
                [
                    *(f"<{argument}>" for argument in leaf.takes),
                    *(f"[{argument}]" for argument in leaf.optional),
                ]
            )
            if leaf.takes or leaf.optional
            else "no arguments"
        )
        print(
            f"pm-ai: `{name} {rest[0]}` takes {expected}\n", file=sys.stderr
        )
        print(usage(group=name), file=sys.stderr)
        return EXIT_USAGE
    return _run(leaf.run, replace(context, arguments=supplied))


def _options(
    words: Sequence[str], *, allowed: tuple[str, ...]
) -> tuple[Mapping[str, str], str | None]:
    """`--name value` and `--name=value` pairs, or the sentence refusing them.

    Hand-rolled for `dispatch`'s own reason: `argparse` answers a parse error by
    raising `SystemExit` from inside the parse, which is the control flow an
    explicitly-passed `argv` exists to avoid.

    A refusal is returned rather than raised because it is a *usage* error, and
    usage errors exit `2` while `Refusal` exits `3`. The four it can name are the
    four ways a command line goes wrong here, and each says which word was the
    problem: a bare positional, an option nobody declared, one given twice, and
    one with nothing after it.
    """
    parsed: dict[str, str] = {}
    remaining = list(words)
    while remaining:
        word = remaining.pop(0)
        if not word.startswith("--"):
            return {}, f"takes options, not `{word}`"
        option, assigned, inline = word[2:].partition("=")
        if option not in allowed:
            spelled = ", ".join(f"--{name}" for name in allowed)
            return {}, f"has no `--{option}` option; it takes {spelled}"
        if option in parsed:
            # Not "last one wins": two values for one option is an operator who
            # believes something other than what would happen, and picking one
            # silently is how they go on believing it.
            return {}, f"was given `--{option}` twice"
        if assigned:
            if not inline:
                # `--scope=` is an operator who meant to type a value and did
                # not. Passed through, it reaches the handler as `""` and is
                # refused as "not a scope" — a message about a word nobody
                # wrote.
                return {}, f"was given `--{option}=` with an empty value"
            parsed[option] = inline
            continue
        if not remaining:
            return {}, f"was given `--{option}` with no value after it"
        if remaining[0].startswith("--"):
            # `--scope --bogus` is a missing value, not a scope named
            # `--bogus`. Swallowing the next option as a value produced a
            # refusal about the wrong word entirely, and hid the option the
            # operator forgot to fill in.
            return {}, (
                f"was given `--{option}` with no value after it — "
                f"`{remaining[0]}` is another option"
            )
        parsed[option] = remaining.pop(0)
    return parsed, None


def _run(handler: Callable[[Context], int], context: Context) -> int:
    """Call one handler, turning its refusal into the code the table assigns.

    Only `Refusal` is caught. Everything else belongs to `pm_ai.app.entry`'s
    guard, which is the one place a traceback is printed — catching broadly here
    would turn a bug into a tidy message and hide it.
    """
    try:
        return handler(context)
    except Refusal as refused:
        print(f"pm-ai: {refused}", file=sys.stderr)
        return EXIT_REFUSAL
