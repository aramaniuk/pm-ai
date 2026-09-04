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
from typing import Protocol, runtime_checkable

from pm_ai.core.config import Config
from pm_ai.core.enrolment import KeyAlreadyEnrolled, enrol
from pm_ai.domain.health import Report
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

    probe_credential: CredentialProbePort = _no_probe
    """Asks a provider whether it accepts a credential (story 8b).

    A value for the same reason `probe_connectors` is one: the adapter lives in
    `pm_ai.connectors`, which this package may not import. Defaulted so every
    existing `Context(...)` in the suite keeps working, and the default refuses
    rather than passing — a probe that answered "fine" without asking would seal
    an unchecked credential, which is the failure 8b's whole ordering exists to
    prevent.
    """

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


TABLE: Mapping[str, Command] = {
    "doctor": Command("check this machine and report what is wrong with it", _doctor),
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


def usage(*, group: str | None = None) -> str:
    """The whole table, or one group's leaves.

    A group with no leaves says so rather than printing an empty list: "not
    implemented yet" and "you typed the wrong leaf name" are different mistakes.
    """
    if group is None:
        lines = ["usage: pm-ai <command> [<subcommand>]", "", "commands:"]
        lines += [f"  {name:<10} {command.summary}" for name, command in TABLE.items()]
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
            name: name + "".join(f" <{argument}>" for argument in leaf.takes)
            for name, leaf in command.leaves.items()
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
        if rest:
            print(f"pm-ai: `{name}` takes no arguments\n", file=sys.stderr)
            print(usage(), file=sys.stderr)
            return EXIT_USAGE
        return _run(command.run, context)
    leaf = command.leaves.get(rest[0]) if rest else None
    if leaf is None or leaf.run is None:
        print(usage(group=name), file=sys.stderr)
        return EXIT_USAGE
    supplied = tuple(rest[1:])
    if len(supplied) != len(leaf.takes):
        # 4j refused every trailing word because no leaf took one. 8b's
        # `connector add` does, so the refusal is now about *arity* — still a
        # refusal, never a silent drop: the flag an operator invented to be
        # careful with must not be the thing that vanishes.
        expected = (
            " ".join(f"<{argument}>" for argument in leaf.takes)
            if leaf.takes
            else "no arguments"
        )
        print(
            f"pm-ai: `{name} {rest[0]}` takes {expected}\n", file=sys.stderr
        )
        print(usage(group=name), file=sys.stderr)
        return EXIT_USAGE
    return _run(leaf.run, replace(context, arguments=supplied))


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
