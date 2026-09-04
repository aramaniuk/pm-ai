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
    4  `doctor` reports a machine that is not healthy

`8b` and `23b` reuse these values and may not add to the table.

## No scheduler, ever

Every subcommand runs once and exits (AD-7, enforced by `cli-owns-no-scheduling`
in `.importlinter`). The 07:00 tick belongs to the daemon.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pm_ai.ports import DaemonPort

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

Nothing in this slice's table raises `Refusal` yet; `4j`'s leaves and `8b`'s
refusals are its callers. Declared here rather than there because the table is
this module's to own, and a slice that had to invent a code would invent a
different one.
"""

EXIT_UNHEALTHY = 4
"""`doctor` ran and the machine is not healthy. Not a failure of `doctor` itself."""


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


@dataclass(frozen=True, slots=True)
class Context:
    """Everything a subcommand may reach, handed in by the composition root."""

    daemon: DaemonPort | None
    """`None` when composition failed — an unenrolled project, an unwritable root,
    an unparseable `config.toml`. `doctor` runs anyway; that is its whole point."""

    diagnose: Callable[[], HealthReport]
    """Runs the startup probes. A callable rather than a report, so a command
    that never asks does not pay for `git --version` and a keychain round trip."""

    def require_daemon(self) -> DaemonPort:
        """The daemon, or a refusal naming what is missing.

        A refusal rather than a crash: a machine with no project enrolled is an
        incomplete setup, which pm-ai understands perfectly and declines to act
        on. `doctor` is the command that works regardless, and it does not call
        this.
        """
        if self.daemon is None:
            raise Refusal(
                "pm-ai could not build a daemon on this machine, so this command "
                "has nothing to run against. `pm-ai doctor` works regardless and "
                "reports why."
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


TABLE: Mapping[str, Command] = {
    "doctor": Command("check this machine and report what is wrong with it", _doctor),
    # Groups with no leaves yet. They are in the table from the start so the
    # shape of the CLI is settled here — `4j` adds `key enrol` and `config show`
    # to this table rather than inventing a second one.
    "key": Command("manage the master key pm-ai seals artifacts with"),
    "config": Command("inspect ~/.pm-ai/config.toml"),
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
        lines += [f"  {name:<10} {leaf.summary}" for name, leaf in command.leaves.items()]
    return "\n".join(lines)


def dispatch(
    argv: Sequence[str],
    *,
    daemon: DaemonPort | None,
    diagnose: Callable[[], HealthReport],
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
    context = Context(daemon=daemon, diagnose=diagnose)
    if not argv:
        # A bare `pm-ai` will open a REPL (CAP-18) and that is `4e`. Until then
        # it must not exit 0: a bare call that silently succeeded is how a broken
        # install reads as a working one.
        print(usage(), file=sys.stderr)
        return EXIT_USAGE
    name, *rest = argv
    if name in _HELP_FLAGS:
        print(usage())
        return EXIT_OK
    command = TABLE.get(name)
    if command is None:
        print(f"pm-ai: unknown command {name!r}\n", file=sys.stderr)
        print(usage(), file=sys.stderr)
        return EXIT_USAGE
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
    return _run(leaf.run, context)


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
