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

import builtins
import contextvars
import getpass
import json
import re
import stat
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any, Protocol, TextIO, runtime_checkable

from pm_ai.core.cadence import CYCLE, FIRST_RETRY, START_SPREAD
from pm_ai.core.config import Config, ConfigRefused, load_config, render_config
from pm_ai.core.enrolment import KeyAlreadyEnrolled, enrol
from pm_ai.core.goal_register import (
    GOALS_SCOPE,
    GoalAbsent,
    GoalChanged,
    GoalRegister,
    GoalUnrecorded,
    MalformedGoals,
    declare_goal,
)
from pm_ai.domain.renders import RenderUnrecorded
from pm_ai.domain.goals import Goal, GoalDomain, GoalHorizon
from pm_ai.domain.event_entries import MalformedEntry, UnknownCategory
from pm_ai.domain.health import Report, UnbuiltConnector
from pm_ai.domain.model_calls import (
    SERVER_LIMITS,
    SUGGESTED_LOCAL_MODEL,
    LocalModelPresence,
    is_cloud_tag,
    normalize_model_name,
)
from pm_ai.core.project_registry import RegistryRefused
from pm_ai.domain.identity import Actor, DataScope, ScopeKind
from pm_ai.domain.scope_model import ScopeResolutionError
from pm_ai.core.meeting_records import (
    MalformedMeeting,
    MeetingNotFound,
    parse_attendees,
)
from pm_ai.core.transcript_details import title_refusal
from pm_ai.core.transcripts import (
    MAX_DROP_MINUTES,
    MIN_DROP_MINUTES,
)
from pm_ai.core.member_updates import MemberRefused, NamePlan
from pm_ai.domain.meetings import AmbiguousMeeting
from pm_ai.domain.meeting_summaries import SummaryRefused
from pm_ai.domain.storage_tiers import UnprotectedCaptureDir
from pm_ai.domain.transcripts import (
    CaptureNotIngested,
    DropInterrupted,
    DropRefused,
    NothingSaid,
    UnboundTranscript,
    UnreadableTranscript,
)
from pm_ai.surfaces.cli.logs import (
    LEVELS as LOG_LEVELS,
    MAX_LINES as LOG_MAX_LINES,
    DaemonLog,
    LogUnreadable,
    level_value,
    lines_value,
    show as show_log,
    unbuffered as unbuffered_stream,
)
from pm_ai.core.connector_enrolment import (
    CategoriesChanged,
    CategoryProjectUnenrolled,
    ConnectorNotConfigured,
    ConnectorRowChanged,
    CredentialNotHeld,
    CredentialStoreUnclaimable,
    MalformedInstanceName,
    MalformedSettings,
    NotAGraphRow,
    OrphanedCredential,
    UnplacedConnector,
    UserNotRecorded,
    assert_enrollable,
    assert_unconfigured,
    connector_configurations,
    enrol_connector,
    stored_credentials,
)
from pm_ai.ports import (
    ArtifactBusy,
    CaptureAlreadyExists,
    EmptyCapture,
    MalformedCaptureName,
    CredentialProbePort,
    DecryptionFailed,
    DaemonPort,
    DaemonRefused,
    DuplicateConnector,
    KeychainPort,
    KeychainUnavailable,
    KeyNotFound,
    ProbeFailed,
    ProbeUnreachable,
    StoragePort,
    UnknownConnectorSystem,
)

__all__ = [
    "Answered",
    "EXIT_OK",
    "EXIT_REFUSAL",
    "EXIT_UNEXPECTED",
    "EXIT_UNHEALTHY",
    "EXIT_USAGE",
    "HARVESTED_WHEN",
    "PROJECT_CLAUSE",
    "Command",
    "ConnectorsOnDisk",
    "DEFAULT_GRAPH_INSTANCE",
    "DROP_ANSWERS",
    "DROP_FORMATS",
    "DROP_WORDS",
    "SUMMARIZE_WORDS",
    "SUMMARY_MAKERS",
    "Context",
    "FirstRun",
    "GoalBook",
    "GoalOutcome",
    "GitLabCheck",
    "GitLabCheckOutcome",
    "GraphCategoryRow",
    "GraphSignIn",
    "GraphSignInOutcome",
    "GraphSignInReplacement",
    "HealthReport",
    "Parsed",
    "RESTART_SENTENCE",
    "Refusal",
    "TABLE",
    "TAKES_A_PATH",
    "TERMINAL_BOUND",
    "TranscriptDrop",
    "DropPreviewOutcome",
    "DroppedFieldOutcome",
    "TranscriptDropOutcome",
    "dispatch",
    "names_a_command",
    "parse",
    "usage",
]

# ── Where a command's words go (story 4r) ────────────────────────────────────

_OUT: contextvars.ContextVar[TextIO | None] = contextvars.ContextVar("pm_ai_cli_out", default=None)
_ERR: contextvars.ContextVar[TextIO | None] = contextvars.ContextVar("pm_ai_cli_err", default=None)
_TERMINAL: contextvars.ContextVar[bool | None] = contextvars.ContextVar(
    "pm_ai_cli_terminal", default=None
)


def _echo(*values: object, file: TextIO | None = None, **options: object) -> None:
    """`print`, to this command's own output streams.

    `dispatch` sets them from `Context.out` and `Context.err` for the length of
    one command, in this thread's context only — never `redirect_stdout`, which
    swaps the process-wide `sys.stdout` and would carry one request's words
    into another's, or onto the daemon's console. With none set they are the
    process's own, read at the call, as `print` reads them.

    A `file` of `sys.stderr` is this command's error stream; anything else is
    its output.
    """
    error = file is not None and file is sys.stderr
    chosen = (_ERR if error else _OUT).get()
    if chosen is None:
        chosen = file if file is not None else sys.stdout
    builtins.print(*values, file=chosen, **options)  # type: ignore[call-overload]


def _stdin_is_terminal() -> bool:
    """Whether this command may ask a question: its standard input is a terminal.

    `False` for a command sent through the daemon (story 4r), whatever the
    daemon's own standard input is: the daemon's terminal is not the client's,
    and a question asked there would be answered by nobody — or by whoever is
    watching the daemon.
    """
    if _TERMINAL.get() is False:
        return False
    return sys.stdin is not None and sys.stdin.isatty()


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

    def local_model(self, name: str) -> LocalModelPresence:
        """Whether `name` is pulled on the local Ollama and runs here (story 7a).

        The local adapter's presence check, bound by the composition root:
        `surfaces` may reach no adapter. Carries no prompt; never raises.
        """
        ...

    @property
    def storage(self) -> StoragePort:
        """A writer over the application scope, for step 4 (story 4p).

        Where a Graph connector's row and sealed sign-in land, and what step 4
        reads `connectors/` through. Its own, because `setup` composes no
        daemon: `Context.daemon` is not there to lend one.
        """
        ...

    @property
    def graph_sign_in(self) -> GraphSignIn:
        """`connector add graph`'s sign-in and checks, over `storage` (story 4p).

        `Context.graph_sign_in` is not used: it is bound to whatever `main`
        composed, and step 4 must write through the writer it checked with.
        Obtaining it says, once, that encryption is off when it is: setup
        builds no daemon, so nothing else printed that before a sign-in is
        sealed.
        """
        ...

    def connectors(self) -> ConnectorsOnDisk:
        """`4n`'s read of `connectors/`, now: step 4's "already done" check."""
        ...


@runtime_checkable
class ConnectorsOnDisk(Protocol):
    """What step 4 needs of `4n`'s read of `connectors/` (story 4p).

    `graph` decides whether the step is already done — an enabled Graph row,
    as an enrolled key is for step 1. `remedy` is `doctor`'s `connectors`
    line's own remediation for this machine, word for word, which is what a
    "no" closes with.
    """

    @property
    def graph(self) -> tuple[str, ...]:
        """Every enabled Graph row's instance name; empty when there is none."""
        ...

    @property
    def remedy(self) -> str:
        """The `connectors` line's remediation, as `doctor` prints it."""
        ...


@runtime_checkable
class GoalReader(Protocol):
    """What `pm-ai goal list` and `goal show` need (story 4t): a read and a path.

    `pm_ai.app.goals.GoalReader` is the real one, built before anything is
    composed at the command line and afresh for every line in the daemon.
    """

    @property
    def path(self) -> Path:
        """Where `strategic_goals.md` lives, whether or not it exists."""
        ...

    def read(self) -> GoalRegister:
        """`strategic_goals.md` as it is now; `MalformedGoals` if unreadable."""
        ...


@runtime_checkable
class GoalBook(Protocol):
    """What `pm-ai goal set`, `update` and `delete` need (stories 22b, 4t).

    `pm_ai.app.goals.GoalBook` is the real one. Named structurally because the
    read, the write and the event-log entry all go through storage, which
    `surfaces` may not reach (AD-30).
    """

    def read(self) -> GoalRegister:
        """`strategic_goals.md` as it is now; `MalformedGoals` if unreadable."""
        ...

    def set(self, goal: Goal) -> GoalOutcome:
        """Merge, write and record one goal; `MalformedGoals` leaves the file be."""
        ...

    def preview(self, goal_id: str) -> GoalPreview:
        """What deleting `goal_id` would remove; writes nothing."""
        ...

    def delete(self, goal_id: str, shown: Sequence[str] | None) -> GoalRemoval:
        """Remove, write and record; `GoalChanged` if `shown` is not what it removes."""
        ...


@runtime_checkable
class GoalPreview(Protocol):
    """`pm_ai.core.goal_register.GoalDeletion`, named by the one field read here."""

    @property
    def removed(self) -> tuple[str, ...]: ...


@runtime_checkable
class GoalRemoval(Protocol):
    """What `GoalBook.delete` did — `pm_ai.app.goals.GoalDeleted`, named by shape."""

    @property
    def path(self) -> Path: ...


@runtime_checkable
class GoalOutcome(Protocol):
    """What `GoalBook.set` did — `pm_ai.app.entry.GoalWritten`, named by shape."""

    @property
    def path(self) -> Path | None: ...
    @property
    def revised(self) -> bool: ...
    @property
    def changed(self) -> bool: ...


@runtime_checkable
class GraphSignInOutcome(Protocol):
    """What a completed Graph sign-in produced — `pm_ai.app.wiring.GraphSignedIn`."""

    @property
    def credential(self) -> str:
        """The value enrolment seals. Never printed."""
        ...

    @property
    def answer(self) -> str:
        """The health check's sentence, for the operator."""
        ...

    @property
    def healthy(self) -> bool:
        """`True` when the check answered `OK`; `False` when it only warned."""
        ...

    @property
    def settings(self) -> Mapping[str, object]:
        """The row's own keys, spelled by the connector — written, never read here."""
        ...


@runtime_checkable
class GraphSignInReplacement(Protocol):
    """What `pm-ai connector sign-in` replaced — `pm_ai.app.wiring.GraphSignInReplaced`."""

    @property
    def answer(self) -> str:
        """The health check's sentence, for the operator."""
        ...

    @property
    def healthy(self) -> bool:
        """`True` when the check answered `OK`; `False` when it only warned."""
        ...


@runtime_checkable
class GraphCategoryRow(Protocol):
    """A Graph row's Outlook categories as read — `pm_ai.app.wiring.GraphCategories` (4s)."""

    @property
    def shown(self) -> object:
        """The row's `categories` value exactly as read, handed back at the save."""
        ...

    @property
    def entries(self) -> tuple[tuple[str, str], ...] | None:
        """(category, project) pairs in the row's order; `None` when unreadable."""
        ...

    @property
    def unreadable(self) -> str | None:
        """Why the row reader refuses the value, when it does."""
        ...


@runtime_checkable
class GraphSignIn(Protocol):
    """`pm-ai connector add graph`'s sign-in and answer checks (story 8l).

    And `pm-ai connector sign-in`'s row reader and keyed replacement (story
    8m): `enrolled` refuses an instance a new sign-in cannot replace, and
    `replace` signs in with the row's own settings and replaces that
    connector's stored sign-in. Their refusals are `pm_ai.core
    .connector_enrolment`'s and `pm_ai.ports`' (AD-49), never the adapter's.

    `pm_ai.app.wiring.GraphEnrolment` is the real one. Named structurally
    because both halves reach `pm_ai.connectors` — the checks a composition
    applies to a Graph row, and Microsoft's device-code sign-in — and
    `surfaces-through-core` forbids this package from importing it. Its
    refusals are the two `pm_ai.ports` declares for a credential check,
    `ProbeFailed` and `ProbeUnreachable` (AD-49).
    """

    @property
    def minimum_window_minutes(self) -> int:
        """CAP-2's floor on the harvest window, stated in the question."""
        ...

    def offered_reach_back_minutes(self, width: int) -> int:
        """What Enter keeps at the reach-back question: five days, or `width` if wider."""
        ...

    @property
    def default_tenant(self) -> str:
        """What Enter keeps at the tenant question."""
        ...

    def settings_file(self, instance: str) -> Path:
        """The row the closing message names."""
        ...

    def ready(self) -> None:
        """Refuse (`ProbeFailed`) when no sign-in could run here — `msal` missing."""
        ...

    def refusal(
        self,
        *,
        client_id: str | None = None,
        tenant: str | None = None,
        width: int | None = None,
        reach_back: int | None = None,
    ) -> str | None:
        """Why the answers given so far would not build a connector, or `None`."""
        ...

    def sign_in(
        self,
        *,
        instance: str,
        client_id: str,
        tenant: str,
        width: int,
        reach_back: int,
        present: Callable[[str], None],
        categories: Mapping[str, str] | None = None,
    ) -> GraphSignInOutcome:
        """Run Microsoft's sign-in through `present`, then the bounded check.

        A non-empty `categories` is saved in the row beside the other settings.
        """
        ...

    def enrolled_projects(self) -> tuple[str, ...]:
        """The enrolled project ids, in the registry's order, read now (story 4s).

        `RegistryRefused` when `projects.toml` cannot be read or parsed.
        """
        ...

    def category_refusal(self, answers: Sequence[tuple[str, str]]) -> str | None:
        """Why these (category, project) answers would not be read, or `None`."""
        ...

    def categories(self, instance: str) -> GraphCategoryRow:
        """`instance`'s Outlook category mapping as its row holds it (story 4s)."""
        ...

    def save_categories(
        self, instance: str, *, shown: object, categories: Mapping[str, str]
    ) -> bool:
        """Save `categories` as `instance`'s mapping if the row still holds `shown`."""
        ...

    def enrolled(self, instance: str) -> None:
        """Refuse an instance whose row or sealed entry a new sign-in cannot replace."""
        ...

    def replace(
        self, *, instance: str, present: Callable[[str], None]
    ) -> GraphSignInReplacement:
        """Sign in with the row's settings, check, and replace this connector's sign-in."""
        ...


@runtime_checkable
class GitLabCheckOutcome(Protocol):
    """What a GitLab check that passed produced — `pm_ai.app.wiring.GitLabChecked`."""

    @property
    def answer(self) -> str:
        """The check's sentence, for the operator."""
        ...

    @property
    def project(self) -> str:
        """The GitLab project path the row records as its `project`."""
        ...

    @property
    def settings(self) -> Mapping[str, object]:
        """The row's other keys, spelled by the connector — written, never read here."""
        ...


@runtime_checkable
class GitLabCheck(Protocol):
    """`pm-ai connector add gitlab`'s checks (story 8o).

    `pm_ai.app.wiring.GitLabEnrolment` is the real one. Named structurally for
    `GraphSignIn`'s reason: judging a host or path with the transport's rules,
    placing a name among the watched projects and asking GitLab all reach
    `pm_ai.connectors`, which `surfaces-through-core` forbids this package.
    Its refusals are `pm_ai.core.connector_enrolment`'s and `pm_ai.ports`'
    (AD-49): `ProbeFailed` for an answer that refused, `ProbeUnreachable` for
    a GitLab that could not be heard from.
    """

    @property
    def default_host(self) -> str:
        """What Enter keeps at the host question."""
        ...

    def settings_file(self, instance: str) -> Path:
        """The row the closing message names."""
        ...

    def ready(self, instance: str) -> None:
        """Refuse what needs the keychain, and a name no start would build."""
        ...

    def assert_placed(self, instance: str) -> None:
        """Refuse a name no start would build, against the projects watched now."""
        ...

    def files_into(self, instance: str) -> str | None:
        """The project `instance` would file into now, or `None` (story 8t)."""
        ...

    def host_as_read(self, typed: str) -> str:
        """The address a typed host is read as: `https://` added to a bare answer."""
        ...

    def refusal(self, *, host: str | None = None, project: str | None = None) -> str | None:
        """Why a typed host or project path would not build a connector, or `None`."""
        ...

    def check(
        self, *, instance: str, host: str, project: str, credential: str
    ) -> GitLabCheckOutcome:
        """Ask GitLab for one commit of `project`, bounded at ten seconds."""
        ...


class DroppedFieldOutcome(Protocol):
    """One decided field — `pm_ai.app.pipelines.DroppedField`, named by shape (story 11e)."""

    @property
    def value(self) -> Any: ...
    @property
    def source(self) -> str: ...
    @property
    def estimated(self) -> bool: ...


@runtime_checkable
class DropPreviewOutcome(Protocol):
    """What `--dry-run` found — `pm_ai.app.pipelines.DropPreview`, named by shape (11e)."""

    @property
    def title(self) -> DroppedFieldOutcome: ...
    @property
    def start(self) -> DroppedFieldOutcome: ...
    @property
    def duration(self) -> DroppedFieldOutcome: ...
    @property
    def participants(self) -> tuple[tuple[str, str | None], ...]: ...
    @property
    def outcome(self) -> str:
        """`join`, `new` or `refused`."""
        ...
    @property
    def meeting_id(self) -> str | None: ...
    @property
    def meeting_title(self) -> str | None: ...
    @property
    def candidates(self) -> tuple[str, ...]: ...
    @property
    def reason(self) -> str: ...
    @property
    def zone(self) -> str: ...
    @property
    def notices(self) -> tuple[str, ...]: ...
    @property
    def found_by(self) -> str: ...
    @property
    def day_unknown(self) -> bool: ...


@runtime_checkable
class TranscriptDropOutcome(Protocol):
    """What a drop did — `pm_ai.app.pipelines.TranscriptDropped`, named by shape."""

    @property
    def capture(self) -> Path: ...
    @property
    def meeting_id(self) -> str: ...
    @property
    def scope(self) -> DataScope: ...
    @property
    def bound_by(self) -> str:
        """`named`, `matched` or `minted`."""
        ...
    @property
    def title(self) -> str: ...
    @property
    def start(self) -> datetime: ...
    @property
    def staged(self) -> int: ...
    @property
    def left(self) -> int: ...
    @property
    def title_field(self) -> DroppedFieldOutcome | None: ...
    @property
    def start_field(self) -> DroppedFieldOutcome | None: ...
    @property
    def duration(self) -> DroppedFieldOutcome | None: ...
    @property
    def participants(self) -> tuple[tuple[str, str | None], ...]: ...
    @property
    def candidates(self) -> tuple[str, ...]: ...
    @property
    def found_by(self) -> str:
        """How a matched record was found: `start` or `same day` (story 11e)."""
        ...
    @property
    def day_unknown(self) -> bool:
        """Minted because the day came only from the modification time (11e)."""
        ...
    @property
    def zone(self) -> str: ...
    @property
    def from_docx(self) -> bool:
        """The capture is the text taken from a `.docx` (story 11d)."""
        ...
    @property
    def notices(self) -> tuple[str, ...]: ...


class TranscriptDrop(Protocol):
    """`pm-ai transcript drop`'s pipeline, bound to this daemon (story 11c).

    `pm_ai.app.pipelines.drop_transcript` is the real one. Named structurally
    because it reaches storage, the meeting records and the skill registry,
    none of which `surfaces` may import. Its refusals are the core's, the
    domain's and `pm_ai.ports`' (AD-49).
    """

    def __call__(
        self,
        *,
        text: str,
        file_name: str,
        scope: DataScope,
        meeting_id: str | None,
        from_docx: bool = False,
        modified: datetime | None = None,
        created: datetime | None = None,
        title: str | None = None,
        start: datetime | None = None,
        duration: int | None = None,
        attendees: tuple[Actor, ...] = (),
        dry_run: bool = False,
    ) -> TranscriptDropOutcome | DropPreviewOutcome: ...


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

    probe_connectors: Callable[[], Report] | None
    """Probes every connector the daemon holds, within CAP-35's bound.

    The same arrangement as `diagnose`, forced by the same contract: `8d`'s
    registry lives in `pm_ai.connectors`, and `.importlinter`'s
    `surfaces-through-core` forbids this package from importing it. So the
    composition root binds the probe to the daemon it built and the report
    arrives as a value. The connectors probed are that daemon's own — the list
    a harvest reads (story 8k) — so this layer never sees a second copy.

    With no daemon it is `None`, and `connector check` refuses through
    `require_daemon()`, the one sentence for a missing daemon. A machine with
    nothing enrolled has a daemon for this command (story 4o) — one that watches
    no project — so its empty report is what says no connectors are registered.

    Typed as `pm_ai.domain.health.Report` rather than structurally, unlike
    `HealthReport` above: that Protocol exists because `pm_ai.platform.doctor`
    is unreachable from here, whereas `pm_ai.domain` is the layer everything may
    name, and the leaf needs `probes` to tell an empty registry from a healthy
    one.
    """

    unbuilt_connectors: tuple[UnbuiltConnector, ...] = ()
    """Every enabled connector row the daemon did not build, with why (4o).

    `connector check` lists each one beside the probed connectors — enrolled,
    not built, and the reason — so no enabled row is omitted, and so that
    `doctor`'s count of enrolled rows (slice `4n`) and this one can be one
    count (AD-39). A disabled row is skipped and not listed. None is
    probed, so none moves the exit code.
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

    **No command consults it since story 8o**: `connector add` enrols `graph`
    and `gitlab` through their own checks and refuses every other system by
    name before asking. Kept as the injection point for a future system whose
    credential is typed and checked through `pm_ai.connectors.probe`.
    """

    graph_sign_in: GraphSignIn | None = None
    """What `pm-ai connector add graph` and `connector sign-in` sign in through.

    It judges each typed answer or the enrolled row, runs Microsoft's sign-in,
    and runs the health check after it. The composition root always supplies
    it — over the application scope's writer when no daemon was built, since
    `connector sign-in` needs no project (story 8m). It is `None` in tests that
    do not need it; a Graph add or sign-in then refuses before asking anything.
    """

    gitlab_enrolment: GitLabCheck | None = None
    """What `pm-ai connector add gitlab` judges its answers and asks GitLab through.

    The composition root supplies it whenever it built a daemon, bound to that
    daemon's writer and watched projects. `None` — a test that does not need
    it, or a CLI composed without it — refuses a GitLab add by name before
    anything is asked.
    """

    first_run: FirstRun | None = None
    """`pm-ai setup`'s keychain, config reader and writer, and fresh probes.

    `None` refuses `setup` as a wiring fault, for `_no_onboarding`'s reason: a
    setup that reported success without enrolling or writing anything would
    leave an operator believing the machine was ready.
    """

    goals: GoalBook | None = None
    """`strategic_goals.md`'s reader and writer. `None` refuses `goal set`."""

    goal_reader: GoalReader | None = None
    """`strategic_goals.md`'s reader alone, for `goal list` and `goal show` (4t)."""

    flags: frozenset[str] = frozenset()
    """The value-less flags this invocation carried (`--yes`), by name (4t)."""

    daemon_log: DaemonLog | None = None
    """Where the daemon's log is, for `pm-ai logs` (story 4w).

    Located by the composition root from paths alone, before anything is
    composed. `None` refuses `logs` as a wiring fault.
    """

    transcript_drop: TranscriptDrop | None = None
    """`pm-ai transcript drop`'s read, bind, capture and ingest (story 11c).

    Bound by the composition root to the daemon it built; `None` with no
    daemon, and the command refuses through `require_daemon()` first.
    """

    transcript_summarize: TranscriptSummarize | None = None
    """`pm-ai transcript summarize`'s request (story 7b), bound only in the daemon.

    `None` everywhere else: a terminal sends the line to the daemon instead,
    and a process with no daemon to send it to refuses naming `pm-ai daemon run`.
    """

    frontier_key: FrontierKeyHand | None = None
    """`pm-ai frontier key`'s hand-over of the key to the running daemon (story 7c).

    The loopback client's `hand_frontier_key`, bound by the composition root;
    `None` refuses the command naming `pm-ai daemon run`.
    """

    members: Members | None = None
    """`pm-ai member name|link|unmatched`'s reads and appends (story 7e).

    `pm_ai.app.members.MemberDesk` over the composed daemon: the client's own
    writer at a terminal, the daemon's handed writer for a line the daemon
    runs. `None` refuses the three commands as a missing daemon.
    """

    serve: Callable[[], int] | None = None
    """Runs the daemon in the foreground until it is stopped (story 4e).

    Injected for `dashboard`'s reason: binding, the token and the server live
    in `pm_ai.app`, which sits above this package. The composition root binds
    it to the daemon it composed — or, with no project enrolled, to one acting
    in pm-ai's own scope and watching none. `None` means no daemon could be
    composed for any other reason, and `daemon run` refuses through
    `require_daemon()`, the one sentence for that.
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

    target: DataScope | None = None
    """What this command acts on, read off its command line by `parse` (4m).

    A project, the personal scope or the application scope, or `None` for a
    command that needs no daemon (`doctor`, `setup`, `project add`). The
    composition root built the daemon to act in exactly this scope, so a
    handler that writes to its target and the daemon's own `scope` cannot
    disagree (AD-11).
    """

    undecided: str | None = None
    """Why no project was selected, on a machine where nothing is wrong.

    Several projects are enrolled and the working directory is inside none of
    them, so pm-ai declined to choose (story 4l). Distinct from `unavailable`
    because the two refusals need different sentences: that one sends the
    operator to `pm-ai doctor`, and here `doctor` will correctly report a
    healthy machine. Passed through verbatim — the composition root is the layer
    that knows the projects, and this layer may not import the registry.
    """

    out: TextIO | None = None
    """Where this command's output goes; `None` is the process's standard output.

    The daemon hands each request its own buffer (story 4r), so nothing one
    command prints reaches another request or the daemon's console.
    """

    err: TextIO | None = None
    """Where this command's error stream goes; `None` is standard error."""

    terminal: bool | None = None
    """`False` when no question may be asked whatever stdin is (story 4r).

    A command sent through the daemon has no terminal of its own, so every
    "stdin is not a terminal" refusal applies to it. `None` asks stdin.
    """

    def require_selection(self) -> None:
        """Refuse when no project was selected; do nothing when one was.

        Consulted only for a command whose target is a project (story 4m, AD-11:
        a command whose target is not a project needs no selection and is never
        refused for the lack of one). The project was named on the command line,
        so the refusal it carries is a named project that is not enrolled.
        """
        if self.undecided:
            # Raised whole rather than wrapped: it already says what is enrolled
            # and what to do, and prefixing "pm-ai could not build a daemon"
            # would report a deliberate no as a broken machine.
            raise Refusal(self.undecided)

    def require_daemon(self) -> DaemonPort:
        """The daemon, or a refusal naming what is missing.

        A refusal rather than a crash: a project target with no project
        enrolled, a registry that cannot be read, a root or `config.toml` that
        will not answer — each is a state pm-ai understands and declines to act
        on. A command aimed at the person or at pm-ai itself gets a daemon on a
        machine with nothing enrolled (story 4o), so "nothing is enrolled"
        reaches here only for a command aimed at a project. `doctor` is the
        command that works regardless, and it does not call this.
        """
        # First, because it is the one case where there is nothing to diagnose
        # — and only for a project target, whose selection can be refused.
        if self.target is not None and self.target.kind is ScopeKind.PROJECT:
            self.require_selection()
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
    Every one of them takes a value; the value-less ones are `flags`, below.
    """

    flags: tuple[str, ...] = ()
    """The long options this command accepts that take no value (story 4t).

    `goal delete --yes` is the first. A flag never takes the word after it as
    its value — `goal delete --yes g_a` reads `g_a` as the id — and a flag given
    a value (`--yes=1`) or given twice is a usage error. Spelled `[--yes]` in
    the usage line. `4w`'s `--follow` and `4u`'s completion read this field.
    """

    composes: bool = True
    """`False` for a command that reads one plain file and needs nothing else.

    `goal list` and `goal show` (story 4t): the composition root hands them a
    reader and composes no daemon, so a broken `config.toml` or no keychain does
    not stop them.
    """

    reads_daemon_log: bool = False
    """`True` for `pm-ai logs` (story 4w): answered before the keychain is built.

    It reads the daemon's log files by their paths and needs no keychain,
    store, settings file or daemon, so the composition root answers it before
    building any of them, and a home with no `~/.pm-ai` gains nothing.
    """

    requires: tuple[str, ...] = ()
    """The members of `options` that must be given (story 11c).

    `transcript drop` is the first command with an option it cannot run
    without: `--scope` names where the meeting is looked for and written, and
    no default would be honest. Missing one is a usage error (`2`), and the
    usage line spells it without brackets. Enforced for a leaf and for a
    command without leaves alike (`_missing`).
    """

    via_daemon: bool = False
    """`True` for a command only the daemon runs, from a terminal too (story 7b).

    `transcript summarize`: typed in a terminal, its line is sent to the
    daemon's `/v1/prompt` and its job followed; it never runs in the CLI's own
    process (AD-21).
    """

    target: Callable[[Mapping[str, str]], DataScope] | None = None
    """Where this command acts, read off its parsed options (story 4m, AD-11).

    `None` is a command that needs no daemon — `doctor`, `setup` and
    `project add` — and every other leaf states its target: a project named
    on its command line, the personal scope, or the application scope.
    Declared on the table so the composition root learns it *before* deciding
    anything about projects, and raises `_Misread` for a word that names no
    scope, which is a usage error rather than a refusal.
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
    _echo(report)
    return EXIT_OK if report.healthy else EXIT_UNHEALTHY


def _daemon_run(context: Context) -> int:
    """`pm-ai daemon run` — story 4e: the daemon, in the foreground, until stopped.

    Everything is the composition root's: this maps its `DaemonRefused` onto
    exit 3, unedited. It is raised for a missing runtime extra; an unreadable
    or refused `config.toml` (on a machine with no project enrolled); an
    operational store that cannot be opened or stays busy, or an unreachable
    keychain, while composing; a taken port or one it cannot listen on; an
    unusable or unreadable token file; a request log that cannot be opened; and
    a server that bound the port but failed to start serving.
    A clean stop (Ctrl-C, a stop signal, the terminal closing) is exit 0, and
    the daemon prints one line saying how it stopped (story 4s).
    """
    if context.serve is None:
        context.require_daemon()
        raise Refusal(
            "no daemon lifecycle was supplied to the CLI, so nothing was "
            "started. This is a wiring fault in pm-ai, not this machine's."
        )
    try:
        return context.serve()
    except DaemonRefused as refused:
        raise Refusal(str(refused)) from refused


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
    _echo(
        f"a master key is enrolled under {name!r}. It stays in the keychain: "
        f"pm-ai fetches it when it seals an artifact, and no command prints it."
    )
    # The daemon asks the keychain once, at its start (AD-5), so a key enrolled
    # now reaches a running daemon only when it restarts — said every time.
    _echo(RESTART_SENTENCE)
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
    rate nor the zone can reach that state: `config.toml` refuses both unset
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
        _echo(f"{name:<{width}}  {value!r:<8}  ({origin})")
    _echo()
    _echo(
        "(default) means the value equals pm-ai's built-in default, and (set) "
        "means it differs. `Config` keeps no provenance, so this compares "
        "values, not origins: a key written into config.toml with the same "
        "value the default already has reads as (default)."
    )
    return EXIT_OK



SUPPORTED_SYSTEMS = ("gitlab", "graph")
"""The systems `pm-ai connector add` enrols, spelled exactly as typed."""


def _connector_add(context: Context) -> int:
    """`pm-ai connector add <system> <instance>` — story 8b's surface.

    A credential is prompted for, never taken as an argument: an argument is
    in the process table while it runs and in shell history afterwards, which
    are two places a token outlives the command that used it.

    Two systems, each with its own path. `graph` is not typed at all (story
    8l): it is four questions, one Outlook category question per enrolled
    project (story 4s) and Microsoft's sign-in, in
    `_connector_add_graph`. `gitlab` is two questions, a hidden prompt and a
    live check, in `_connector_add_gitlab` (story 8o). Any other word —
    `GitLab` included, since the system is a key spelled exactly — is refused
    by name before anything is asked: no other system has a check, and
    prompting for a credential nothing can check would only invite one to be
    typed for nothing.
    """
    system, instance = context.arguments
    if system not in SUPPORTED_SYSTEMS:
        raise Refusal(
            f"pm-ai cannot enrol a {system!r} connector: the systems it enrols "
            f"are {' and '.join(repr(s) for s in SUPPORTED_SYSTEMS)}, spelled "
            f"exactly so. Nothing was asked and nothing was saved."
        )
    daemon = context.require_daemon()

    with _enrolment_refusals():
        if system == "graph":
            graph = context.graph_sign_in
            if graph is None:
                raise Refusal(
                    "no Graph sign-in was supplied to the CLI, so a Graph connector "
                    "cannot be enrolled. That is a wiring fault in pm-ai, not anything "
                    "about this machine; nothing was asked and nothing was saved."
                )
            return _connector_add_graph(daemon.storage, graph, instance)
        return _connector_add_gitlab(context, daemon, instance)


def _connector_add_graph(storage: StoragePort, graph: GraphSignIn, instance: str) -> int:
    """`pm-ai connector add graph <instance>`: questions, sign-in, check, save (8l).

    The order is the point, and each step is placed where its refusal costs the
    least:

    1. **What can be known without asking**, before anything is asked: the
       name is free, the master key is reachable to seal with, and `msal` is
       installed — nobody should finish a sign-in only to be told the name
       was taken or the result cannot be saved.
    2. **A terminal**, for the same reason: the questions and the sign-in wait
       on a human.
    3. **Four questions**: the app (client) id, required; the tenant, where
       Enter keeps the default and a typed answer is accepted when it is a
       tenant id (a GUID), a domain, or one of `organizations`, `common` and
       `consumers` in any case, and stored as typed; the harvest window, with its
       minimum stated and no default; the first-run reach-back, at least the
       window, where Enter keeps five days (7200 minutes) or the window when
       it is wider, the question naming the number either way (story 8s). An
       answer that would not build a connector is refused with its reason and
       asked again, never saved.
    4. **One Outlook category question per enrolled project** (story 4s), in
       the registry's order, read when these questions start: which category
       marks that project's meetings, Enter for none. Answers are trimmed and
       judged together by the rules the row is read with; one category given
       to two projects is refused naming both and the later question asked
       again. Before the sign-in, because a refused category costs a re-typed
       line and an interruption after the sign-in would throw a sign-in away.
    5. **Microsoft's sign-in, then the bounded health check**, behind
       `context.graph_sign_in`. Either refusing saves nothing.
    6. **Enrolment**, which seals the sign-in and writes the settings —
       the category mapping among them — together, as for every other
       connector. Another add of the same name that finishes during this
       one's sign-in is caught by enrolment's own re-check, at the cost of
       this sign-in.

    The sign-in token never reaches this function's output: it is handed to
    enrolment as the credential, and enrolment's own redaction applies to the
    one sentence printed from the check.

    Takes the writer and the sign-in rather than a context, so `pm-ai setup`'s
    step 4 (story 4p) runs this same path with its own two — setup composes no
    daemon. Its refusals leave raw; each caller words them.
    """
    assert_enrollable(storage, system="graph", instance=instance)
    graph.ready()

    if not _stdin_is_terminal():
        raise Refusal(
            "adding a Graph connector asks its questions and waits for a "
            "Microsoft sign-in in a browser, so it needs a terminal. stdin is "
            "not a TTY here — this is a pipe, a cron job or a CI step. Nothing "
            "was asked and nothing was saved. Run `pm-ai connector add graph` "
            "from an interactive shell."
        )

    client_id = _ask_until(
        "Microsoft app (client) id (required): ",
        judge=lambda answer: graph.refusal(client_id=answer),
    )
    tenant = (
        _ask_until(
            f"Tenant [Enter keeps {graph.default_tenant}]: ",
            judge=lambda answer: graph.refusal(tenant=answer) if answer else None,
        )
        or graph.default_tenant
    )
    minimum = graph.minimum_window_minutes
    width = _ask_minutes(
        f"Harvest window in minutes (at least {minimum}; no default): ",
        judge=lambda minutes: graph.refusal(width=minutes),
    )
    offered = graph.offered_reach_back_minutes(width)
    reach_back = _ask_minutes(
        f"First-run reach-back in minutes (at least {width}, the window) "
        f"[Enter keeps {offered}]: ",
        judge=lambda minutes: graph.refusal(width=width, reach_back=minutes),
        default=offered,
    )
    mapping = dict(_ask_categories(graph, _projects(graph)))

    _echo()
    signed = graph.sign_in(
        instance=instance,
        client_id=client_id,
        tenant=tenant,
        width=width,
        reach_back=reach_back,
        present=_echo,
        categories=mapping,
    )
    # The sign-in and the check have both answered; enrolment re-runs the
    # name check under its own reading of the stores and seals. Its "probe" is
    # the check that just ran, so it is reported rather than repeated.
    answer = enrol_connector(
        storage,
        system="graph",
        instance=instance,
        credential=signed.credential,
        probe=lambda system, credential: signed.answer,
        settings=signed.settings,
    )

    _echo(answer)
    state = (
        "enrolled and healthy"
        if signed.healthy
        else "enrolled; its health check warned (above)"
    )
    _echo(f"{instance} is {state}. {HARVESTED_WHEN}")
    _echo(_mapped(instance, mapping))
    if mapping:
        _echo(_project_clause(instance, mapping.values()))
    _echo(f"Its settings are in {graph.settings_file(instance)}.")
    return EXIT_OK


def _projects(graph: GraphSignIn) -> tuple[str, ...]:
    """The enrolled projects the category questions offer, read now (story 4s)."""
    try:
        return graph.enrolled_projects()
    except RegistryRefused as unreadable:
        raise Refusal(str(unreadable)) from unreadable
    except (OSError, ValueError) as unread:
        # Anything else reading the application's settings raised meanwhile
        # (a `config.toml` that stopped parsing, a file that stopped opening).
        raise Refusal(
            f"the enrolled projects could not be read ({type(unread).__name__}: "
            f"{unread}), so which projects a category could file into is "
            f"unknown. Nothing was saved."
        ) from unread


_NOTHING_TO_REMOVE = "there is nothing to remove; press Enter for none."


def _ask_categories(
    graph: GraphSignIn,
    projects: Sequence[str],
    current: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[tuple[str, str], ...]:
    """One Outlook category question per project, in order; the accepted answers (4s).

    The one function `connector add graph`, setup's step 4 and `connector
    categories` ask through. `current` is each project's categories now:
    Enter keeps them (or none), a typed category replaces them — the current
    one re-typed in another case is kept in its stored spelling — and `-`
    removes them. `-` is offered only where there is something to remove, and
    refused elsewhere, so no category is ever `-`. Every answer is trimmed and
    judged with the answers before it by `graph.category_refusal`; a refused
    one is said and that question asked again. Ctrl-C or Ctrl-D at a question
    is `_typed`'s refusal: nothing was saved.

    Returns (category, project) pairs, a project kept with several categories
    giving one pair each.
    """
    held_now = current or {}
    answers: list[tuple[str, str]] = []
    for project in projects:
        held = held_now.get(project, ())
        if held:
            spelled = ", ".join(repr(category) for category in held)
            offer = f"Enter keeps {spelled}; - removes {'it' if len(held) == 1 else 'them'}"
        else:
            offer = "Enter for none"
        prompt = f"Outlook category that marks {project}'s meetings [{offer}]: "
        while True:
            typed = _typed(prompt)
            if typed is None:
                _echo(f"  {_NOT_TEXT}", file=sys.stderr)
                continue
            if typed == "-":
                if not held:
                    _echo(f"  {_NOTHING_TO_REMOVE}", file=sys.stderr)
                    continue
                chosen: tuple[str, ...] = ()
            elif not typed:
                chosen = held
            else:
                same = [c for c in held if c.strip().casefold() == typed.casefold()]
                chosen = (same[0] if same else typed,)
            proposal = [*answers, *((category, project) for category in chosen)]
            reason = graph.category_refusal(tuple(proposal))
            if reason is None:
                answers = proposal
                break
            # Enter keeps what is held, so where something is held Enter would
            # meet the same refusal again: the way out is another or `-`.
            remedy = (
                f"Type another for {project}, or - to remove it."
                if held
                else f"Type another for {project}, or press Enter for none."
            )
            _echo(f"  {reason} {remedy}", file=sys.stderr)
    return tuple(answers)


def _listed(names: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _mapped(instance: str, mapping: Mapping[str, str]) -> str:
    """What the category mapping now files where, and the command that changes it."""
    command = f"`pm-ai connector categories {instance}`"
    if not mapping:
        return (
            f"No Outlook category is mapped to a project, so every meeting is "
            f"filed as personal; {command} maps them."
        )
    filed = _listed([f"{category!r} into {project}" for category, project in mapping.items()])
    return (
        f"Meetings tagged in Outlook are filed by category: {filed}. Every other "
        f"meeting is filed as personal; {command} changes this."
    )


def _project_clause(instance: str, projects: Iterable[str]) -> str:
    """`PROJECT_CLAUSE` for the projects a row files into, then `RESTART_SENTENCE`."""
    named = sorted(set(projects))
    which = named[0] if len(named) == 1 else "one of them"
    return (
        PROJECT_CLAUSE.format(instance=instance, projects=_listed(named), which=which)
        + " "
        + RESTART_SENTENCE
    )


def _connector_add_gitlab(context: Context, daemon: DaemonPort, instance: str) -> int:
    """`pm-ai connector add gitlab <instance>`: questions, token, check, save (8o).

    1. **What can be known without asking**, in this order: a CLI with no
       GitLab check; the name's shape and a row already using it; a terminal;
       then the keychain — no master key, one that cannot be read, or a
       credential sealed with no row (half-enrolled); then, on a machine
       watching several projects, a name no start would build.
    2. **Two questions**: the host, where Enter keeps the default and a typed
       answer must be an https address with no query, fragment, user info or
       trailing slash — one typed with no scheme is read as `https://` and
       saved so, one line saying "taken as …" (story 8s), while one naming a
       scheme is judged as typed; the project path, required, as `group/project`. An
       answer that would not build a connector is asked again with its reason.
    3. **The token**, at a hidden prompt, stripped; blank is refused, and
       Ctrl-C or Ctrl-D ends the command with nothing saved.
    4. **The check**: one commit of that project on that host, behind
       `context.gitlab_enrolment`, bounded at ten seconds. Any refusal saves
       nothing.
    5. **The name is placed again**, against the projects watched now, since
       one may have been enrolled meanwhile.
    6. **Enrolment**, which seals the token and writes the row — `project`
       and `host` with it — together.

    Ctrl-C during the keychain read or the check ends the command saying
    nothing was saved, as it does at a question.

    The token is never printed: it goes to the check and to enrolment, and
    both redact what they say.
    """
    gitlab = context.gitlab_enrolment
    if gitlab is None:
        raise Refusal(
            "no GitLab check was supplied to the CLI, so a GitLab connector "
            "cannot be enrolled. That is a wiring fault in pm-ai, not anything "
            "about this machine; nothing was asked and nothing was saved."
        )

    # One enrolled GitLab instance per project scope (AD-10, story 8t): a
    # second row filing into a project that has one is refused, naming it.
    assert_unconfigured(
        daemon.storage, system="gitlab", instance=instance, files_into=gitlab.files_into
    )
    if not _stdin_is_terminal():
        raise Refusal(
            "adding a GitLab connector asks two questions and takes a token at "
            "a hidden prompt, so it needs a terminal. stdin is not a TTY here — "
            "this is a pipe, a cron job or a CI step — and prompting would echo "
            "the token and leave it in history. Nothing was asked and nothing "
            "was saved. Run `pm-ai connector add gitlab` from an interactive "
            "shell."
        )
    with _interruptible():
        gitlab.ready(instance)

    typed_host = _ask_until(
        f"GitLab host [Enter keeps {gitlab.default_host}]: ",
        judge=lambda answer: gitlab.refusal(host=answer) if answer else None,
    )
    host = gitlab.host_as_read(typed_host) if typed_host else gitlab.default_host
    if typed_host and host != typed_host:
        # Said straight after the question, so it sits above anything the
        # check says later (story 8s).
        _echo(f"  taken as {host}")
    project = _ask_until(
        "GitLab project path, as group/project (required): ",
        judge=lambda answer: gitlab.refusal(project=answer),
    )
    token = _hidden(
        "GitLab personal access token with the read_api scope (not shown as "
        "you type): "
    )
    if not token:
        raise Refusal("no token was typed, so nothing was saved.")

    with _interruptible():
        try:
            checked = gitlab.check(
                instance=instance, host=host, project=project, credential=token
            )
        except ProbeUnreachable as silent:
            # Its sentence already says the token was not judged, so it is not
            # given `_enrolment_refusals`' suffix saying so a second time.
            raise Refusal(str(silent)) from silent
    # Projects may have been enrolled while the questions were answered, so
    # the name is placed again against the registry as it is now: a
    # credential is never sealed for a name a start would not build.
    gitlab.assert_placed(instance)
    # The check has answered; enrolment re-runs the name check under its own
    # reading of the stores and seals. Its "probe" is the check that just ran,
    # so it is reported rather than repeated.
    answer = enrol_connector(
        daemon.storage,
        system="gitlab",
        instance=instance,
        credential=token,
        probe=lambda system, credential: checked.answer,
        settings=checked.settings,
        project=checked.project,
    )

    _echo(answer)
    _echo(f"{instance} is enrolled and healthy. {HARVESTED_WHEN}")
    files_into = gitlab.files_into(instance)
    if files_into is not None:
        _echo(_project_clause(instance, (files_into,)))
    _echo(f"Its settings are in {gitlab.settings_file(instance)}.")
    return EXIT_OK


_INTERRUPTED = "interrupted before every question was answered, so nothing was saved."


@contextmanager
def _interruptible() -> Iterator[None]:
    """Ctrl-C during a step nobody is typing at — a keychain read, a check.

    Ends the command with the sentence an interrupted question gives, rather
    than a traceback: nothing has been saved before enrolment's own writes.
    """
    try:
        yield
    except KeyboardInterrupt as stopped:
        raise Refusal("interrupted; nothing was saved.") from stopped


def _hidden(prompt: str) -> str:
    """One line typed at the terminal without echo, stripped.

    Ctrl-D and Ctrl-C end the command with `_typed`'s refusal: nothing has
    been saved at any question. An answer that is not text in the terminal's
    encoding is asked again, as `_ask_until` asks.
    """
    while True:
        try:
            return getpass.getpass(prompt).strip()
        except UnicodeDecodeError:
            _echo(f"  {_NOT_TEXT}", file=sys.stderr)
        except (EOFError, KeyboardInterrupt) as closed:
            raise Refusal(_INTERRUPTED) from closed


def _typed(prompt: str) -> str | None:
    """One line typed at the terminal, stripped; `None` for one that was not text.

    Ctrl-D and Ctrl-C are an interruption rather than an answer, and nothing
    has been saved at any question, so the refusal says so.
    """
    try:
        return input(prompt).strip()
    except UnicodeDecodeError:
        return None
    except (EOFError, KeyboardInterrupt) as closed:
        raise Refusal(_INTERRUPTED) from closed


_NOT_TEXT = "that answer was not valid text in this terminal's encoding."


def _ask_until(prompt: str, *, judge: Callable[[str], str | None]) -> str:
    """Ask until `judge` accepts the answer, printing its reason each time it refuses."""
    while True:
        answer = _typed(prompt)
        reason = _NOT_TEXT if answer is None else judge(answer)
        if reason is None and answer is not None:
            return answer
        _echo(f"  {reason}", file=sys.stderr)


def _ask_minutes(
    prompt: str, *, judge: Callable[[int], str | None], default: int | None = None
) -> int:
    """A whole number of minutes, asked until `judge` accepts it.

    Plain digits only, after trimming: `1440.0`, `1_440` and `+480` are all
    numbers to Python and none of them is what the question asked for, so each
    is asked again rather than read generously. Blank keeps `default` when the
    caller offers one — the Graph reach-back does (story 8s), and its prompt
    names the number — and is asked again otherwise: the harvest window and
    the drop duration keep no default, and Enter taking one there would be the
    answer the spec reserves for the human. A default is judged like a typed
    answer, so an offer the check would refuse is never saved.
    """
    while True:
        typed = _typed(prompt)
        if typed is None:
            _echo(f"  {_NOT_TEXT}", file=sys.stderr)
            continue
        if not typed and default is None:
            _echo("  this has no default; type a whole number of minutes.", file=sys.stderr)
            continue
        if not typed:
            typed = str(default)
        if not (typed.isascii() and typed.isdigit()):
            _echo(
                f"  {typed!r} is not a whole number of minutes; type digits only.",
                file=sys.stderr,
            )
            continue
        minutes = int(typed)
        reason = judge(minutes)
        if reason is None:
            return minutes
        _echo(f"  {reason}", file=sys.stderr)


@contextmanager
def _enrolment_refusals(
    *,
    command: str = "connector add",
    then: str = "then enrol the connector",
) -> Iterator[None]:
    """Every refusal an enrolment can meet, as the sentence `Refusal` carries.

    `command` and `then` word the two sentences composed here for the command
    meeting them: `connector add` and `connector sign-in` share the rest.
    """
    try:
        yield
    except (
        DuplicateConnector,
        MalformedInstanceName,
        MalformedSettings,
        UnplacedConnector,
    ) as refused:
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
            f"be sealed. Run `pm-ai key enrol` first, {then}. "
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
    except OSError as failed:
        # The claim's lock file is a directory or unreadable, or the filesystem
        # has no locks (story `1r`) — or any other fault the operating system
        # names. Its own message names the file; a traceback would read as a bug.
        raise Refusal(f"{command} stopped: {failed}") from failed


def _connector_sign_in(context: Context) -> int:
    """`pm-ai connector sign-in <instance>`: redo an enrolled Graph connector's sign-in (8m).

    For a connector whose stored sign-in has gone bad — a rotated token that
    could not be saved, one Microsoft expired or revoked, conditional access
    wanting a human, a permission granted after enrolment. It asks nothing but
    the sign-in: the app id and tenant are the row's own, judged first by the
    checks every start applies.

    1. **What can be known without asking**: the row exists, says `graph` and
       would build; the sealed entry, if any, is Graph's; the master key is
       reachable; `msal` is installed. Then a terminal.
    2. **Microsoft's sign-in, then the bounded health check**, as `connector
       add graph` runs them. Either failing saves nothing.
    3. **The credential, then the row's user id**, under the credentials
       file's claim, after the row is judged again. Any authenticated account
       is accepted — identity is Microsoft's to guard — and whoever signed in
       is recorded. A row write that fails after the credential landed says
       exactly that and exits 3.

    Needs no daemon and no enrolled project: every artifact it touches is in
    the application scope. `enabled` is neither read nor written — it governs
    harvesting, not enrolment. The daemon never runs this on its own (AD-39).
    """
    (instance,) = context.arguments
    graph = context.graph_sign_in
    if graph is None:
        raise Refusal(
            "no Graph sign-in was supplied to the CLI, so a Graph connector's "
            "sign-in cannot be redone. That is a wiring fault in pm-ai, not "
            "anything about this machine; nothing was asked and nothing was saved."
        )
    with _sign_in_refusals():
        graph.enrolled(instance)
        graph.ready()
        if not _stdin_is_terminal():
            raise Refusal(
                "redoing a sign-in waits for a Microsoft sign-in in a browser, so "
                "it needs a terminal. stdin is not a TTY here — this is a pipe, a "
                "cron job or a CI step. Nothing was asked and nothing was saved. "
                f"Run `pm-ai connector sign-in {instance}` from an interactive "
                f"shell."
            )
        replaced = graph.replace(instance=instance, present=_echo)

    _echo(replaced.answer)
    state = "healthy" if replaced.healthy else "warned (above)"
    _echo(
        f"{instance}: sign-in replaced; {state}. No restart is needed: a running "
        f"daemon reads the new sign-in on its next refresh."
    )
    return EXIT_OK


@contextmanager
def _sign_in_refusals() -> Iterator[None]:
    """`connector sign-in`'s refusals: enrolment's, plus the six only it meets.

    `UserNotRecorded` is the one after which something was saved: the new
    sign-in landed, so the check's answer is printed before the refusal, which
    still exits 3 as the story's matrix gives it.
    """
    try:
        with _enrolment_refusals(
            command="connector sign-in", then="then run this command again"
        ):
            yield
    except UserNotRecorded as unrecorded:
        _echo(unrecorded.answer)
        raise Refusal(str(unrecorded)) from unrecorded
    except (
        ConnectorNotConfigured,
        ConnectorRowChanged,
        CredentialNotHeld,
        CredentialStoreUnclaimable,
        NotAGraphRow,
    ) as refused:
        raise Refusal(str(refused)) from refused


def _connector_categories(context: Context) -> int:
    """`pm-ai connector categories <instance>`: which Outlook category files where (4s).

    For any enrolled Graph row, enabled or not, at any time after enrolment.

    1. **What can be known without asking**: the row exists, is a JSON object
       and is Graph's; the registry can be read. Then a terminal.
    2. **The mapping as the row holds it**: each category and its project, or
       none. An entry naming a project no longer enrolled is shown as dropped
       on save (a row holding it is not built) and not asked about; a value
       the row reader refuses is shown as written and replaced by the answers.
       With no project enrolled there is nothing to map: exit 0.
    3. **One question per enrolled project**, through `_ask_categories`, the
       function `connector add graph` asks through, each offering its current
       answer.
    4. **The save**: only the `categories` key, under enrolment's claim, after
       the row is read again — refused, writing nothing, if its categories
       changed meanwhile, the row is gone, or a project was removed from the
       registry. A mapping equal to the one shown is not written.

    Talks to no Microsoft service, composes no harvest and needs no project.
    A meeting already filed stays where it was; the next harvest files by the
    new mapping, since every job reads the row afresh.
    """
    (instance,) = context.arguments
    graph = context.graph_sign_in
    if graph is None:
        raise Refusal(
            "no Graph enrolment was supplied to the CLI, so a Graph connector's "
            "categories cannot be mapped. That is a wiring fault in pm-ai, not "
            "anything about this machine; nothing was asked and nothing was saved."
        )
    with _categories_refusals():
        row = graph.categories(instance)
        projects = _projects(graph)
        if not _stdin_is_terminal():
            raise Refusal(
                "mapping Outlook categories asks one question per enrolled "
                "project, so it needs a terminal. stdin is not a TTY here — this "
                "is a pipe, a cron job or a CI step. Nothing was asked and "
                f"nothing was saved. Run `pm-ai connector categories {instance}` "
                f"from an interactive shell."
            )
        enrolled = set(projects)
        stale = _show_categories(instance, row, enrolled)
        if not projects:
            _echo(
                "No project is enrolled, so there is nothing to map; nothing was "
                "asked or written."
            )
            return EXIT_OK
        entries = row.entries or ()
        current = {
            project: tuple(category for category, named in entries if named == project)
            for project in projects
        }
        mapping = dict(_ask_categories(graph, projects, current))
        if row.entries is not None and not stale and mapping == dict(row.entries):
            _echo(f"Nothing changed, so nothing was written: {instance}'s mapping is as shown.")
            return EXIT_OK
        written = graph.save_categories(instance, shown=row.shown, categories=mapping)
    if not written:
        # The row already held these answers when it was read again under the
        # claim, so nothing changed and no daemon has anything new to see.
        _echo(f"Nothing changed, so nothing was written: {instance}'s mapping is already this.")
        return EXIT_OK

    _echo(f"Saved. {_mapped(instance, mapping)}")
    _echo(
        "A meeting already filed stays where it was; the next harvest files by "
        "this mapping, and a running daemon reads it there without a restart."
    )
    if mapping:
        _echo(_project_clause(instance, mapping.values()))
    return EXIT_OK


def _show_categories(
    instance: str, row: GraphCategoryRow, enrolled: set[str]
) -> tuple[str, ...]:
    """Print the mapping as the row holds it; return the projects of stale entries."""
    _echo(f"{instance}'s Outlook categories now:")
    if row.entries is None:
        written = json.dumps(row.shown, ensure_ascii=False)
        _echo(
            f"  written as {written}, which pm-ai cannot read: "
            f"{_sentence(row.unreadable or 'the row reader refuses it')} The "
            f"answers below replace it."
        )
        return ()
    if not row.entries:
        _echo("  none — every meeting is filed as personal")
        return ()
    stale: list[str] = []
    for category, project in row.entries:
        if project in enrolled:
            _echo(f"  {category!r} → {project}")
        else:
            stale.append(project)
            _echo(
                f"  {category!r} → {project}: {project} is no longer enrolled, so "
                f"this entry is dropped when the answers are saved — a row "
                f"naming it is not built"
            )
    return tuple(stale)


@contextmanager
def _categories_refusals() -> Iterator[None]:
    """`connector categories`' refusals, each the sentence `Refusal` carries."""
    try:
        with _enrolment_refusals(
            command="connector categories", then="then run this command again"
        ):
            yield
    except (
        CategoriesChanged,
        CategoryProjectUnenrolled,
        ConnectorNotConfigured,
        CredentialStoreUnclaimable,
        NotAGraphRow,
        RegistryRefused,
    ) as refused:
        raise Refusal(str(refused)) from refused


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

    An empty report exits `0` and says so. A machine with nothing enrolled has a
    daemon for this command that watches no project (story 4o), so it holds no
    built-in connector, and that is a first run rather than a broken one —
    there is no claim of reachability to be false. A connector that *is*
    registered and answers `ABSENT` is a different state and exits `4`: setup
    is incomplete, harvests are being skipped, and `Health.ABSENT` is
    expressly not a pass.

    **Every enabled row is listed, built or not** (AD-39, story 4o). A row the
    daemon did not build — a GitLab row with no project to file into, a Graph
    row a settings key short, a row naming no usable instance or system or a
    system with no adapter, an adapter that would not construct — is printed as
    enrolled, not built, with the sentence the build gave. It is not probed, so
    it moves no exit code. A **disabled** row is skipped and not listed: the
    operator turned it off. Listing every enabled row is what lets `doctor`'s
    count (slice `4n`) and this one be one count.

    **Its target is the application scope** (story 4m), so it needs no
    project selection: the daemon is composed with every enrolled project
    watched from any folder, and the connectors it probes are that daemon's
    own (story 8k). Until 4m this command was refused outside every folder on a
    machine with two projects, because no daemon was composed there.

    With no daemon — a registry that cannot be read, a root or `config.toml`
    fault — no probe is passed and this refuses through `require_daemon()`
    (exit 3), naming the reason: there are no connectors this machine would
    harvest from, so there is nothing honest to probe.
    """
    probe_all = context.probe_connectors
    if probe_all is None:
        context.require_daemon()
        # A daemon with no probe bound to it: `pm_ai.app.entry` binds one to
        # every daemon it builds, so reaching here is pm-ai's own wiring fault.
        raise Refusal(
            "no connector probe was supplied to the CLI although a daemon was "
            "built. That is a wiring fault in pm-ai, not anything about this "
            "machine's connectors."
        )
    report = probe_all()
    unbuilt = context.unbuilt_connectors
    if not report.probes and not unbuilt:
        _echo(
            "no connectors are registered, so there is nothing to probe. That is "
            "an ordinary first-run state: connectors are registered when the "
            "daemon is composed."
        )
        return EXIT_OK
    for probe in report.probes:
        _echo(probe)
    for row in unbuilt:
        _echo(row)
    _echo()
    if not report.probes:
        _echo(
            f"no connector was built, so nothing was probed; "
            f"{len(unbuilt)} enrolled, not built."
        )
        return EXIT_OK
    if unbuilt:
        _echo(f"{len(unbuilt)} enrolled, not built, and not probed.")
    # Not `_echo(report)`: `Report.__str__` ends in "pm-ai is healthy.", which is
    # a claim about the machine. This command asked about providers, and the two
    # verdicts are not interchangeable — `doctor` can pass while every connector
    # is refused.
    verdict = (
        "every connector answered."
        if report.healthy
        else "not every connector is healthy."
    )
    _echo(f"{len(report.probes)} connectors probed; {verdict}")
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


class _Misread(Exception):
    """A command line `parse` cannot read, carrying the sentence that says why.

    Private and caught in `parse`, which turns it into a usage error (`2`)
    before anything is composed — never a `Refusal`, which is a command line
    pm-ai read and declined.
    """


PERSONAL = DataScope(ScopeKind.PERSONAL)
APPLICATION = DataScope(ScopeKind.APPLICATION)


def _personal(options: Mapping[str, str]) -> DataScope:
    return PERSONAL


def _application(options: Mapping[str, str]) -> DataScope:
    return APPLICATION


def _goals_target(options: Mapping[str, str]) -> DataScope:
    return GOALS_SCOPE


def _dashboard_target(options: Mapping[str, str]) -> DataScope:
    """`--scope`'s scope, personal when absent; `_Misread` when it names none."""
    requested = options.get(SCOPE_ARGUMENT)
    scope = _scope(requested)
    if scope is None:
        raise _Misread(
            f"`--{SCOPE_ARGUMENT} {requested}` is not a scope. Write "
            f"`personal`, or `project:<id>` naming an enrolled project."
        )
    return scope


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
    # The target `parse` read off `--scope`, not a second reading of the option:
    # the command line is read once (AD-11), and the daemon was composed to act
    # in exactly this scope, so the dashboard and everything recorded on the
    # way to it land in one place.
    scope = context.target
    if scope is None:
        # A wiring fault rather than a deliberate no, so not a `Refusal`: it
        # reaches `main`'s guard and exits 1 with a traceback, like any bug.
        raise RuntimeError(
            "no target scope reached the dashboard command, so nothing was "
            "rendered. That is a wiring fault in pm-ai, not anything about this "
            "machine."
        )
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
    except RenderUnrecorded as unrecorded:
        # Story 9b: the page was written and recording its instant failed.
        # The written line first, so the output says the file changed; then
        # the refusal, exit 3.
        if unrecorded.path is not None:
            _echo(f"the {scope} dashboard is written: {unrecorded.path}")
        raise Refusal(str(unrecorded)) from unrecorded
    except ArtifactBusy as claimed:
        # Story 9b: another command holds the dashboard's claim. At a terminal
        # that is a refusal naming it; a job sent through the daemon fails with
        # it, for the job's own record (story 4r).
        if context.terminal is False:
            raise
        raise Refusal(str(claimed)) from claimed
    _echo(f"the {scope} dashboard is written: {written}")
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
    except (ScopeResolutionError, RegistryRefused, ArtifactBusy) as refused:
        raise Refusal(str(refused)) from refused
    except OSError as failed:
        # Wraps all of onboarding, so the operating system's own error — which
        # names its file — is the report, not a guess at which file it was.
        raise Refusal(f"project add stopped: {failed}") from failed
    if outcome.already_registered:
        _echo(
            f"{outcome.project_id} is already onboarded at {outcome.repository}; "
            f"nothing changed."
        )
    else:
        _echo(f"{outcome.project_id} is onboarded at {outcome.repository}")
        _echo(f"git exclusion rules are in {outcome.gitignore}")
    # Enrolled projects are read once, at the daemon's start (AD-5): said every
    # time this succeeds, an already-enrolled project included — a daemon
    # started before it was first enrolled still does not watch it.
    _echo(RESTART_SENTENCE)
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


class _SetupStopped(Refusal):
    """Setup's own "no admissible answer" refusal, already worded for its step."""


def _answer[T](
    question: str,
    interpret: Callable[[str], T],
    *,
    step: str,
    default: str | None = None,
    done: str = "Every earlier step stays done",
) -> T:
    """Ask until `interpret` accepts an answer, at most `SETUP_ATTEMPTS` times.

    `interpret` raises `_Inadmissible` or `ConfigRefused` for an answer it will
    not take, and both are printed and asked again. `_Inadmissible` is the
    prompt's own refusal — a rate that is not a number, a flag that is not y or
    n. `ConfigRefused` comes from `Config.__post_init__` when the interpreted
    answer is built into a `Config`: a negative or non-finite rate, an unknown
    zone, a zone carrying an unpaired surrogate. Letting it escape here would
    be a traceback after the key and the project are already done.
    """
    prompt = f"{question} [{default}]: " if default else f"{question}: "
    for _ in range(SETUP_ATTEMPTS):
        try:
            return interpret(_ask(prompt))
        except (_Inadmissible, ConfigRefused) as refused:
            _echo(f"  {refused}", file=sys.stderr)
    raise _SetupStopped(
        f"setup stopped at {step}: no admissible answer to {question!r} after "
        f"{SETUP_ATTEMPTS} attempts. {done}; run `pm-ai setup` again to "
        f"continue from here."
    )


def _setup_key(first_run: FirstRun) -> None:
    """Step 1: the master key — `4b`'s `enrol`, and an enrolled key is done.

    First because every encrypted write refuses without it. `KeyAlreadyEnrolled`
    is a completed step here and not the refusal `pm-ai key enrol` makes of it:
    the operator asked for a ready machine, not for a new key, and one is
    there. Nothing replaces it — `enrol` never does.
    """
    _echo("[1/4] master key — first, because every encrypted write refuses without it")
    try:
        name = enrol(first_run.keychain)
    except KeyAlreadyEnrolled:
        _echo("      already enrolled; the keychain was not written.")
        return
    except KeychainUnavailable as unreachable:
        raise Refusal(
            f"setup stopped at step 1 (master key) and nothing after it ran: "
            f"{unreachable}"
        ) from unreachable
    _echo(f"      a master key is enrolled under {name!r}; no command prints it.")
    _echo(f"      {RESTART_SENTENCE}")


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
    _echo("[2/4] project — second, because no path resolves until one is registered")
    path = _answer("project directory", _project_answer, step="step 2 (project)")
    alias = _answer(
        "project id (empty to use the directory's name)",
        lambda raw: raw.strip() or None,
        step="step 2 (project)",
    )
    try:
        outcome = context.onboard(path, alias)
    except (ScopeResolutionError, RegistryRefused, ArtifactBusy) as refused:
        raise Refusal(
            f"setup stopped at step 2 (project); step 1 stays done: {refused}"
        ) from refused
    except OSError as unwritable:
        raise Refusal(
            f"setup stopped at step 2 (project); step 1 stays done: {unwritable}"
        ) from unwritable
    if outcome.already_registered:
        _echo(
            f"      {outcome.project_id} is already registered at "
            f"{outcome.repository}; nothing changed."
        )
    else:
        _echo(f"      {outcome.project_id} is registered at {outcome.repository}")
        _echo(f"      {RESTART_SENTENCE}")


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


def _y_or_n(raw: str) -> bool | None:
    """Setup's y/n answer: `True`, `False`, or `None` for an empty one."""
    text = raw.strip().lower()
    if not text:
        return None
    if text in _YES or text in _NO:
        return text in _YES
    raise _Inadmissible(f"{raw.strip()!r} is not y or n.")


def _verbose(current: Config) -> Callable[[str], Config]:
    def interpret(raw: str) -> Config:
        answer = _y_or_n(raw)
        return current if answer is None else replace(current, verbose_logging=answer)

    return interpret


_NO_MODEL = frozenset({"none", "-"})
"""The answers that leave `local_model` unset at step 3 (story 7a)."""


def _model(
    current: Config, first_run: FirstRun, seen: list[LocalModelPresence]
) -> Callable[[str], Config]:
    """Step 3's `local_model` answer: Enter takes the offered model, `none` leaves it unset.

    A cloud tag is refused at the prompt and asked again, and so is a name the
    server's `/api/show` says runs elsewhere — no prompt is ever sent to
    either (AD-1 class LM). The presence check's answer is kept in `seen` so
    the step can print it once the file is written.
    """

    def interpret(raw: str) -> Config:
        text = raw.strip() or SUGGESTED_LOCAL_MODEL
        if text.lower() in _NO_MODEL:
            return current
        settled = replace(current, local_model=text)
        if is_cloud_tag(text):
            raise _Inadmissible(
                f"{text} has a cloud tag: the Ollama server forwards it to Ollama's "
                f"hosted service, so a prompt would leave this machine. Name a model "
                f"that runs locally; OLLAMA_NO_CLOUD=1 turns cloud models off on the "
                f"server."
            )
        presence = first_run.local_model(text)
        if presence.runs_elsewhere:
            raise _Inadmissible(presence.probe.detail)
        seen.append(presence)
        return settled

    return interpret


def _setup_config(first_run: FirstRun, existing: bytes | None, current: Config) -> None:
    """Step 3: `config.toml` — asked for, rendered by `4g`, written once.

    After the key and the project because its absence is survivable: every
    key is optional. Which keys are asked is decided from what `load_config` read, not
    from the bytes, so a file holding only a BOM, comments or whitespace is a
    first-run file:

    - **first run** — `load_config` gives `Config()`: every key is asked;
    - **partial** — the rate or the zone is still at its unset state (`0.0`,
      `""`): only those are asked, and every key the file sets is kept as it
      is. `verbose_logging` is not asked again: `false` is a value, not an
      unset state, so a file cannot be told apart from one that chose it;
    - **complete** — neither is unset: a completed step, as an enrolled key
      is, and nothing is asked or written.

    `local_model` (story 7a) is asked after the existing keys whenever it is
    unset, offering `SUGGESTED_LOCAL_MODEL`; `none` leaves it unset. The name
    is checked with the local adapter's presence check, a model running
    elsewhere refused at the prompt, and the result printed — with
    `ollama pull <name>` when the model is not there. pm-ai never pulls it.

    Each answer is a `Config` before it is accepted, so `__post_init__` refuses
    it at the prompt that asked. Who the PM is is not asked: it comes from the
    Graph connector rows (story 2o).
    """
    _echo("[3/4] config.toml — after the project, because every key in it is optional")
    first_run_file = current == Config()
    ask_rate = not current.blended_hourly_rate
    ask_zone = not current.display_timezone
    ask_model = not current.local_model
    if not first_run_file and not ask_rate and not ask_zone and not ask_model:
        _echo("      already configured; nothing was written.")
        return
    step = "step 3 (config.toml)"
    settled = current
    if ask_rate:
        # `0.0` is the rate's unset state, not a value: `config.toml` refuses
        # an explicit zero, so there is never a current rate to offer here.
        settled = _answer(
            "blended_hourly_rate (empty to leave unset)", _rate(settled), step=step
        )
    if ask_zone:
        settled = _answer(
            "display_timezone, e.g. Europe/Warsaw (empty to leave unset)",
            _zone(settled),
            step=step,
        )
    if first_run_file:
        settled = _answer(
            "verbose_logging (y/n)",
            _verbose(settled),
            step=step,
            default="y" if settled.verbose_logging else "n",
        )
    checked: list[LocalModelPresence] = []
    if ask_model:
        # Story 7a: after the existing keys. pm-ai never pulls a model.
        settled = _answer(
            "local_model, the Ollama model pm-ai reads transcripts with "
            "(Enter for the offered one, `none` to leave unset)",
            _model(settled, first_run, checked),
            step=step,
            default=SUGGESTED_LOCAL_MODEL,
        )
    if not first_run_file and settled == current:
        # Only `local_model` was asked and it was left unset: the file says
        # what it said, so it is not rewritten (a rewrite drops comments).
        _echo("      already configured (local_model left unset); nothing was written.")
        return
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
    _echo(f"      written to {written}")
    if checked and settled.local_model:
        presence = checked[-1]
        # The line without its remedy, then the one remedy that fits: the pull
        # command only when the server answered and does not list the model.
        _echo(f"      {replace(presence.probe, remediation='')}")
        if presence.not_pulled:
            _echo(f"      pull it with: ollama pull {normalize_model_name(settled.local_model)}")
            _echo(f"      {SERVER_LIMITS}")
        elif presence.probe.remediation:
            _echo(f"      {presence.probe.remediation}")


DEFAULT_GRAPH_INSTANCE = "graph:work"
"""The name Enter keeps at step 4's name question, when it is free (story 4p)."""

GRAPH_QUESTION = "add a Microsoft Graph connector now? (y/n)"
"""Step 4's one question; Enter is no (story 4p)."""

_STEP_4 = "step 4 (Microsoft Graph connector)"

_STEPS_DONE = "Steps 1–3 stay done"

_NOTHING_SAVED = "nothing was saved for the connector"

_RUN_AGAIN = (
    "Run `pm-ai setup` again, or `pm-ai connector add graph <name>`, to add one."
)

_STATE_UNREADABLE = (
    DecryptionFailed,
    KeychainUnavailable,
    KeyNotFound,
    ArtifactBusy,
    OSError,
    ValueError,
)
"""What reading back the sealed store and `connectors/` can raise."""


class _StepFour:
    """What step 4 has chosen so far, so its ending can say what is on disk."""

    def __init__(self) -> None:
        self.storage: StoragePort | None = None
        self.instance: str | None = None


def _graph_instance(storage: StoragePort) -> str:
    """Step 4's name question, each typed name checked by `assert_enrollable`.

    Enter keeps `DEFAULT_GRAPH_INSTANCE` only when that name is free — no row
    and no sealed entry under it, disabled or not. When it is in use, or
    whether it is free could not be checked (the store busy or unreadable),
    the question offers no default and a blank answer is asked again. A
    malformed or in-use name is named and asked again, at most
    `SETUP_ATTEMPTS` times. Setup checks the name first so that the shared
    path's own check of it cannot fail unless the machine changed meanwhile.
    """
    default: str | None = None
    try:
        assert_enrollable(storage, system="graph", instance=DEFAULT_GRAPH_INSTANCE)
        default = DEFAULT_GRAPH_INSTANCE
        question = "connector name"
    except DuplicateConnector:
        question = (
            f"connector name ({DEFAULT_GRAPH_INSTANCE} is in use; type another, "
            f"as graph:<name>)"
        )
    except (ArtifactBusy, OSError):
        # Not a reason to stop: a typed name is checked on its own, and that
        # check names whatever is wrong.
        question = (
            f"connector name (whether {DEFAULT_GRAPH_INSTANCE} is free could not "
            f"be checked; type one, as graph:<name>)"
        )

    def interpret(raw: str) -> str:
        typed = raw.strip()
        if not typed:
            if default is not None:
                return default
            raise _Inadmissible("a name is required here; there is no default.")
        try:
            assert_enrollable(storage, system="graph", instance=typed)
        except (MalformedInstanceName, DuplicateConnector) as refused:
            raise _Inadmissible(str(refused)) from refused
        return typed

    return _answer(
        question,
        interpret,
        step=_STEP_4,
        default=default,
        done=f"{_STEPS_DONE} and {_NOTHING_SAVED}",
    )


def _graph_step(first_run: FirstRun, step: _StepFour) -> None:
    """Step 4's body: the done check, the question, then `connector add graph`."""
    on_disk = first_run.connectors()
    if on_disk.graph:
        _echo(
            f"      already enrolled: {', '.join(on_disk.graph)}; nothing was "
            f"asked or written. Another is added with `pm-ai connector add graph "
            f"<name>`."
        )
        return
    wanted = _answer(
        GRAPH_QUESTION,
        lambda raw: _y_or_n(raw) is True,
        step=_STEP_4,
        default="n",
        done=_STEPS_DONE,
    )
    if not wanted:
        _echo("      skipped; nothing was asked about Graph and nothing was written.")
        _echo(f"      {on_disk.remedy}")
        return
    storage = first_run.storage
    step.storage = storage
    graph = first_run.graph_sign_in
    # Before the name question: a missing runtime extra is said before any
    # connector question is asked, as `connector add graph` says it.
    graph.ready()
    step.instance = _graph_instance(storage)
    code = _connector_add_graph(storage, graph, step.instance)
    if code != EXIT_OK:
        raise Refusal(f"adding {step.instance} ended with exit code {code}.")


def _setup_connector(first_run: FirstRun) -> None:
    """Step 4: an optional Microsoft Graph connector (story 4p).

    Done when an enabled Graph row exists, as an enrolled key is: its names are
    printed and nothing is asked or written. Otherwise one y/n question, Enter
    meaning no. "yes" asks the name, then runs `connector add graph`'s
    questions, sign-in, check and save through `_connector_add_graph` itself,
    never a copy of it. Those questions keep the command's own behaviour inside
    setup: they are asked again until answered, with no attempt limit, as in
    the command; Ctrl-C or Ctrl-D leaves them.

    Every way the step ends early — the read of `connectors/` included — is
    setup's refusal at step 4, naming steps 1–3 as done, an interruption
    anywhere in it included, so the shared command's own interrupt sentence is
    not what is printed. What it says was saved is read back from the sealed
    store and `connectors/` at that moment, never assumed: a credential sealed
    with no row, or a connector fully enrolled before the interruption, is
    said as such.
    """
    _echo(
        "[4/4] Microsoft Graph connector — optional: the machine is ready without "
        "one, and adding one signs in at Microsoft in a browser and needs an app "
        "(client) id"
    )
    step = _StepFour()
    try:
        with _enrolment_refusals(
            command="connector add graph", then="then run `pm-ai setup` again"
        ):
            _graph_step(first_run, step)
    except (KeyboardInterrupt, EOFError) as stopped:
        raise _step_four_ended(step, None) from stopped
    except _SetupStopped:
        raise
    except Refusal as refused:
        cause = refused.__cause__
        if isinstance(cause, (KeyboardInterrupt, EOFError)):
            raise _step_four_ended(step, None) from refused
        raise _step_four_ended(step, refused) from refused


def _saved(step: _StepFour) -> tuple[str, str | None]:
    """What is on disk for the connector now, and the one remedy that fits it.

    Read back rather than inferred from where the step stopped: an
    interruption can land between the seal and the row, or after both.
    """
    instance, storage = step.instance, step.storage
    if instance is None or storage is None:
        return _NOTHING_SAVED, _RUN_AGAIN
    try:
        sealed = instance in stored_credentials(storage)
        row = instance in connector_configurations(storage)
    except _STATE_UNREADABLE as unread:
        return (
            f"whether anything was saved for {instance} could not be read back "
            f"({unread})",
            "`pm-ai doctor` names the connectors enrolled.",
        )
    if sealed and row:
        return (
            f"{instance} WAS saved — its settings row and sealed sign-in are both "
            f"on disk, so it is enrolled and the next command or daemon job uses it",
            None,
        )
    if sealed:
        return (
            f"{instance}'s sign-in is sealed in the credentials file and its "
            f"settings row was not written, so it is NOT enrolled and will not "
            f"harvest",
            f"No pm-ai command removes a sealed entry yet, so {instance} stays in "
            f"use; enrol the connector under a different name with "
            f"`pm-ai setup` or `pm-ai connector add graph <name>`.",
        )
    if row:
        return (
            f"{instance}'s settings row is on disk with no sealed sign-in, so it "
            f"will not harvest",
            f"`pm-ai connector sign-in {instance}` signs it in.",
        )
    return _NOTHING_SAVED, _RUN_AGAIN


_CARRIES_REMEDY = ("`pm-ai ", "uv sync")
"""Text in a refusal that already tells the person what to run."""


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _step_four_ended(step: _StepFour, refused: Refusal | None) -> Refusal:
    """Step 4's ending as setup's refusal: the cause, what is on disk, one remedy.

    `refused` is `None` for an interruption. The orphaned-credential refusal's
    own sentence is replaced by what `_saved` reads back, because its remedy —
    run the add once more — is refused as a name in use.
    """
    state, remedy = _saved(step)
    if refused is None:
        cause = f"setup was interrupted at {_STEP_4}."
    else:
        inner = refused.__cause__
        if isinstance(inner, OrphanedCredential):
            failed = inner.__cause__
            reason = f"its settings row could not be written: {failed}" if failed else str(inner)
        else:
            reason = str(refused)
        cause = f"setup stopped at {_STEP_4}: {_sentence(reason)}"
        if state == _NOTHING_SAVED and any(m in reason for m in _CARRIES_REMEDY):
            remedy = None  # the cause already says what to run
    parts = [cause, f"{_STEPS_DONE}; {state}."]
    if remedy:
        parts.append(remedy)
    return Refusal(" ".join(parts))


def _setup(context: Context) -> int:
    """`pm-ai setup` — key, project, config, Graph, then `1g`'s probes (stories 4h, 4p).

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
    if not _stdin_is_terminal():
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
    _setup_connector(first_run)
    # Story 7c: after step 4, the frontier key — entered once the daemon runs.
    _echo()
    _echo(
        "Optional: to let `pm-ai transcript summarize --by claude` ask Claude, start "
        "the daemon (`pm-ai daemon run`) and run `pm-ai frontier key`."
    )

    _echo()
    report = first_run.diagnose()
    _echo(report)
    return EXIT_OK if report.healthy else EXIT_UNHEALTHY


# ── `pm-ai goal` (stories 22b, 4t) ───────────────────────────────────────────

GOAL_OPTIONS = ("domain", "horizon", "title")
"""What `goal set` and `goal update` take, so a goal can be written without a
question. The values are read by `declare_goal`'s closed spellings; nothing here
decides what a goal may be."""

YES_FLAG = "yes"
"""`goal delete --yes`: skip the confirmation, for a script."""

_GOAL_QUESTIONS = {
    "domain": "domain (project, team or personal)",
    "horizon": "horizon (short, medium or long)",
    "title": "title",
}


def _goal_prompt(question: str, default: str | None, *, command: str = "goal set") -> str:
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
            f"{command} was interrupted at a prompt, so nothing was written."
        ) from closed
    return answer if answer.strip() or default is None else default


def _goal_book(context: Context) -> GoalBook:
    context.require_daemon()
    goals = context.goals
    if goals is None:
        raise Refusal(
            "no goal writer was supplied to the CLI, so nothing was written. "
            "This is a wiring fault in pm-ai, not something wrong with the goal."
        )
    return goals


def _goal_reader(context: Context) -> GoalReader:
    reader = context.goal_reader
    if reader is None:
        raise Refusal(
            "no goal reader was supplied to the CLI, so nothing was read. This "
            "is a wiring fault in pm-ai, not something wrong with the goals."
        )
    return reader


def _spelled_options(names: Sequence[str]) -> str:
    return _listed_words([f"`--{name} <{name}>`" for name in names])


def _listed_words(words: Sequence[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _goal_set(context: Context) -> int:
    """`pm-ai goal set [<id>] [--domain] [--horizon] [--title]` — create or revise.

    The create path (story 22b), which since `4t` also takes its values as
    options, so a goal can be written from a script. One rule for questions:
    for an id on file, any option given means nothing else is asked; none at a
    terminal asks all three, offering the current values; none without a
    terminal is refused. For a new id, each value not given is asked at a
    terminal and named in the refusal without one. With no id, the id is asked
    first at a terminal and named without one.
    """
    return _goal_write(context, command="goal set", creates=True)


def _goal_update(context: Context) -> int:
    """`pm-ai goal update <id> [options]` — revise a goal on file, never create one.

    The same rule for questions as `goal set`; an id not on file is refused
    naming `goal set`, so a mistyped id never creates a goal (story 4t).
    """
    return _goal_write(context, command="goal update", creates=False)


def _goal_write(context: Context, *, command: str, creates: bool) -> int:
    """`goal set` and `goal update`: collect the three values, then hand them to `22b`'s writer.

    Nothing is decided here — the closed vocabularies and the id charset are
    `declare_goal`'s, the merge is `set_goal`'s — and every refusal is passed
    through verbatim. The file is read before the first question, so a
    `strategic_goals.md` the parser refuses is refused before anything is
    asked, and is not written. Nothing is written before any refusal.
    """
    given = {name: context.options[name] for name in GOAL_OPTIONS if name in context.options}
    terminal = _stdin_is_terminal()
    named = context.arguments[0] if context.arguments else None
    if named is None and not terminal:
        missing = ["`<id>`", *(f"`--{name} <{name}>`" for name in GOAL_OPTIONS if name not in given)]
        raise Refusal(
            f"{command} without a terminal cannot ask, so it needs "
            f"{_listed_words(missing)} on its command line. stdin is not a TTY "
            f"here — a pipe, a cron job or a CI step — so nothing was asked and "
            f"nothing was written."
        )
    goals = _goal_book(context)
    try:
        register = goals.read()
        goal_id = (
            named
            if named is not None
            else _goal_prompt("goal id, e.g. g_payments_latency", None, command=command)
        ).strip()
        current = register.get(goal_id)
        if current is None and not creates:
            raise Refusal(
                f"{goal_id!r} is not a goal in strategic_goals.md, so nothing was "
                f"written. `goal update` revises a goal on file and never creates "
                f"one; `pm-ai goal set {goal_id}` creates it, and `pm-ai goal "
                f"list` lists the ids on file."
            )
        values = _goal_values(current, given, terminal=terminal, command=command)
        goal = declare_goal(goal_id=goal_id, scope=GOALS_SCOPE, **values)
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
        _echo(f"{goal.goal_id} is unchanged; nothing was written or recorded.")
        return EXIT_OK
    # The verb from the read `set` merged onto, not from `register` above: the
    # file may have gained this id while the prompts were open.
    verb = "revised" if outcome.revised else "set"
    _echo(f"{goal.goal_id} is {verb} in {outcome.path}")
    return EXIT_OK


def _goal_values(
    current: Goal | None, given: Mapping[str, str], *, terminal: bool, command: str
) -> dict[str, str]:
    """The domain, horizon and title to declare, by `4t`'s one rule for questions."""
    if current is not None:
        on_file = {
            "domain": current.domain.value,
            "horizon": current.horizon.value,
            "title": current.title,
        }
        if given:
            return {name: given.get(name, on_file[name]) for name in GOAL_OPTIONS}
        if not terminal:
            raise Refusal(
                f"{command} {current.goal_id} without a terminal cannot ask what "
                f"to change, so it needs at least one of "
                f"{_spelled_options(GOAL_OPTIONS)}. Nothing was written."
            )
        return {
            name: _goal_prompt(_GOAL_QUESTIONS[name], on_file[name], command=command)
            for name in GOAL_OPTIONS
        }
    missing = [name for name in GOAL_OPTIONS if name not in given]
    if missing and not terminal:
        raise Refusal(
            f"{command} without a terminal cannot ask, so a new goal needs "
            f"{_spelled_options(missing)} on its command line. Nothing was written."
        )
    values = dict(given)
    for name in missing:
        values[name] = _goal_prompt(_GOAL_QUESTIONS[name], None, command=command)
    return {name: values[name] for name in GOAL_OPTIONS}


def _goal_delete(context: Context) -> int:
    """`pm-ai goal delete <id> [--yes]` — remove a goal and its detail lines (story 4t).

    At a terminal the lines it will remove are printed first and a `(y/n)` is
    asked; `y` or `yes`, in any case and padding, deletes, and anything else —
    end of input and Ctrl-C included — writes nothing. The file is read again
    after the answer, and the delete is refused if the block is not the one
    shown. `--yes` skips the question; without a terminal it is required.
    """
    goal_id = context.arguments[0]
    confirm = YES_FLAG not in context.flags
    if confirm and not _stdin_is_terminal():
        raise Refusal(
            f"goal delete asks before it removes anything, and stdin is not a "
            f"TTY here — a pipe, a cron job or a CI step. Give `--{YES_FLAG}` to "
            f"delete without the question. Nothing was deleted."
        )
    goals = _goal_book(context)
    shown: tuple[str, ...] | None = None
    try:
        if confirm:
            # `preview` reads and refuses as `delete` does — an id not on file
            # is `GoalAbsent` from either — so no separate read comes first.
            try:
                preview = goals.preview(goal_id)
            except (OSError, ArtifactBusy) as unreadable:
                raise Refusal(
                    f"strategic_goals.md could not be read, so nothing was "
                    f"deleted: {unreadable}"
                ) from unreadable
            _echo(f"goal delete {goal_id} removes these lines from strategic_goals.md:")
            for line in preview.removed:
                _echo(f"  {line}")
            if not _confirmed(f"delete {goal_id}? (y/n): "):
                raise Refusal(f"{goal_id} was not deleted; nothing was written.")
            shown = preview.removed
        try:
            deleted = goals.delete(goal_id, shown)
        except (OSError, ArtifactBusy) as unwritable:
            raise Refusal(
                f"strategic_goals.md could not be written, so nothing changed: "
                f"{unwritable}"
            ) from unwritable
    except GoalAbsent:
        raise Refusal(
            f"{goal_id!r} is not a goal in strategic_goals.md, so nothing was "
            f"deleted. `pm-ai goal list` lists the ids on file."
        ) from None
    except (MalformedGoals, GoalUnrecorded, GoalChanged) as refused:
        raise Refusal(str(refused)) from refused
    _echo(
        f"{goal_id} is deleted from {deleted.path}. The dashboard's goals "
        f"section drops it at its next render."
    )
    return EXIT_OK


def _confirmed(prompt: str) -> bool:
    """A `(y/n)` answer: `y` or `yes`, any case and padding, is yes; all else is no."""
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt):
        # The terminal's cursor is still on the prompt line; the refusal that
        # follows starts on a line of its own.
        _echo()
        return False
    except UnicodeDecodeError:
        return False
    return answer.strip().casefold() in {"y", "yes"}


_HORIZON_WIDTH = max(len(f"({horizon.value})") for horizon in GoalHorizon)
"""`(medium)`'s width, so every title in `goal list` starts in one column."""


def _goal_list(context: Context) -> int:
    """`pm-ai goal list` — the goals on file, grouped by domain (story 4t).

    The dashboard's order (`rendering._strategic_milestones`): `GoalDomain`'s,
    and by id within a domain. Only reads, and needs no daemon.
    """
    register = _read_goals(context)
    if not register:
        if register.present:
            _echo("strategic_goals.md holds no goals yet; `pm-ai goal set` adds one.")
        else:
            _echo("No goals are set yet; `pm-ai goal set` creates strategic_goals.md with the first.")
        return EXIT_OK
    for domain in GoalDomain:
        tier = sorted(
            (goal for goal in register.values() if goal.domain is domain),
            key=lambda goal: goal.goal_id,
        )
        if not tier:
            continue
        _echo(domain.value.capitalize())
        width = max(len(goal.goal_id) for goal in tier)
        for goal in tier:
            horizon = f"({goal.horizon.value})"
            _echo(f"  {goal.goal_id:<{width}}  {horizon:<{_HORIZON_WIDTH}}  {goal.title}")
    return EXIT_OK


def _goal_show(context: Context) -> int:
    """`pm-ai goal show <id>` — one goal, its citation form and its file (story 4t)."""
    goal_id = context.arguments[0]
    register = _read_goals(context)
    goal = register.get(goal_id)
    if goal is None:
        raise Refusal(
            f"{goal_id!r} is not a goal in strategic_goals.md. `pm-ai goal list` "
            f"lists the ids on file."
        )
    rows = (
        ("id", goal.goal_id),
        ("cited as", f"goal:{goal.goal_id}"),
        ("domain", goal.domain.value),
        ("horizon", goal.horizon.value),
        ("title", goal.title),
        ("file", str(_goal_reader(context).path)),
    )
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        _echo(f"{label:<{width}}  {value}")
    return EXIT_OK


def _read_goals(context: Context) -> GoalRegister:
    """The register, or the parser's own refusal (its line included)."""
    try:
        return _goal_reader(context).read()
    except MalformedGoals as refused:
        raise Refusal(str(refused)) from refused
    except (OSError, ArtifactBusy) as unreadable:
        raise Refusal(f"strategic_goals.md could not be read: {unreadable}") from unreadable


# ── `pm-ai transcript drop` (story 11c) ──────────────────────────────────────

MEETING_ARGUMENT = "meeting"
"""`transcript drop`'s optional `--meeting <id>`: the record to bind to."""

TITLE_OPTION = "title"
START_OPTION = "start"
ATTENDEES_OPTION = "attendees"
DURATION_OPTION = "duration"
DROP_ANSWERS = (TITLE_OPTION, START_OPTION, ATTENDEES_OPTION, DURATION_OPTION)
"""What `transcript drop` may be told about the meeting, as options (stories 11d, 11e).

Nothing is asked: each one given wins over what pm-ai would read from the file,
the records or the file's times. Each is checked as it is read, and one the
rule refuses is a usage error."""

DRY_RUN_FLAG = "dry-run"
JSON_FLAG = "json"
DROP_FLAGS = (DRY_RUN_FLAG, JSON_FLAG)
"""`--dry-run` previews what a drop would store and writes nothing; `--json`,
only with it, prints the preview as one JSON object (story 11e)."""

DROP_WORDS = ("transcript", "drop")

DROP_FORMATS = "The transcript formats pm-ai reads: .vtt, .txt, .md, .docx."
"""Said at the end of every refusal about the dropped file (story 11d). Added
here, where the command turns a refusal into its sentence, never inside the
readers, whose refusals Graph's transcript path shares."""

RELATIVE_PATHS = "a relative <file> is read from the folder pm-ai was started in"

_DROPPABLE_KINDS = frozenset({ScopeKind.PERSONAL, ScopeKind.PROJECT})

START_FORMS = ("2026-10-10T14:00+03:00", "2026-10-10T11:00Z")
"""The spellings `--start` takes: a date-time with its offset, or in UTC with
`Z`. Seconds and fractions of a second are accepted too."""

_START = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$"
)

MAX_DROP_BYTES = 16 * 1024 * 1024
"""The largest file `transcript drop` reads: 16 MiB.

A day-long meeting's WebVTT is a few megabytes; anything past this is not a
transcript somebody typed or downloaded, and reading it whole would be the
first thing to go wrong.
"""

_NOT_A_TRANSCRIPT_FORMAT = {
    ".doc": "a Word document in the old .doc format",
    ".pdf": "a PDF",
    ".rtf": "a rich-text document",
}
"""Document formats refused by name (story 11c). `.docx` is read since `11d`."""

_DOCX = ".docx"


def _drop_target(options: Mapping[str, str]) -> DataScope:
    """`--scope`'s scope, required; `_Misread` when absent or naming none.

    `people:<id>` and `application` parse, and the command refuses them by
    name (`3`): they are scopes pm-ai read and declines to drop into.
    """
    requested = options.get(SCOPE_ARGUMENT)
    scope = None if requested is None else _scope(requested)
    if scope is None:
        raise _Misread(
            f"`transcript drop` needs `--{SCOPE_ARGUMENT} personal` or "
            f"`--{SCOPE_ARGUMENT} project:<id>` naming an enrolled project"
            + ("" if requested is None else f"; `{requested}` is not a scope")
            + "."
        )
    return scope


def _start_refusal(answer: str) -> str | None:
    if _START.match(answer):
        try:
            datetime.fromisoformat(answer)
        except ValueError:
            pass
        else:
            return None
    return (
        f"{answer!r} is not a start pm-ai can read. Give a date and time with "
        f"its offset from UTC, as {START_FORMS[0]}, or in UTC, as {START_FORMS[1]}."
    )


def _title_refusal(answer: str) -> str | None:
    # One rule for a typed title and one read from the file (story 11e).
    return title_refusal(answer)


def _attendee_refusal(answer: str) -> str | None:
    try:
        parse_attendees(answer)
    except MalformedMeeting as refused:
        return (
            f"{refused} Give handles separated by commas with no spaces, as "
            f"email:ana@example.com,email:bo@example.com; a handle cannot "
            f"carry a comma."
        )
    return None


def _duration_refusal(minutes: int) -> str | None:
    if MIN_DROP_MINUTES <= minutes <= MAX_DROP_MINUTES:
        return None
    return (
        f"{minutes} minutes is not a meeting's length here; give "
        f"{MIN_DROP_MINUTES} to {MAX_DROP_MINUTES}."
    )


@dataclass(frozen=True, slots=True)
class _Given:
    """The options given, each checked; `None` where none was given."""

    title: str | None = None
    start: datetime | None = None
    attendees: tuple[Actor, ...] | None = None
    duration: int | None = None


class _BadAnswer(Exception):
    """An option its rule would not accept: a usage error."""


def _given(options: Mapping[str, str]) -> _Given:
    """The meeting options, each judged by its rule."""
    title = options.get(TITLE_OPTION)
    start = options.get(START_OPTION)
    attendees = options.get(ATTENDEES_OPTION)
    duration = options.get(DURATION_OPTION)
    if title is not None:
        title = title.strip()
        reason = _title_refusal(title)
        if reason is not None:
            raise _BadAnswer(f"`--{TITLE_OPTION}`: {reason}")
    if start is not None:
        start = start.strip()
        reason = _start_refusal(start)
        if reason is not None:
            raise _BadAnswer(f"`--{START_OPTION}`: {reason}")
    if attendees is not None:
        attendees = attendees.strip()
        reason = _attendee_refusal(attendees)
        if reason is not None:
            raise _BadAnswer(f"`--{ATTENDEES_OPTION}`: {reason}")
    minutes: int | None = None
    if duration is not None:
        typed = duration.strip()
        if not (typed.isascii() and typed.isdigit()):
            raise _BadAnswer(
                f"`--{DURATION_OPTION}` takes a whole number of minutes, "
                f"{MIN_DROP_MINUTES} to {MAX_DROP_MINUTES}; {duration!r} is not one."
            )
        minutes = int(typed)
        reason = _duration_refusal(minutes)
        if reason is not None:
            raise _BadAnswer(f"`--{DURATION_OPTION}`: {reason}")
    if MEETING_ARGUMENT in options and any(name in options for name in DROP_ANSWERS):
        named = _listed_words([f"`--{name}`" for name in DROP_ANSWERS if name in options])
        raise _BadAnswer(
            f"`--{MEETING_ARGUMENT}` names the record to bind to, so {named} "
            f"cannot be given with it: they describe a meeting to match or make."
        )
    return _Given(
        title=title,
        start=None if start is None else datetime.fromisoformat(start).astimezone(timezone.utc),
        attendees=None if attendees is None else parse_attendees(attendees),
        duration=minutes,
    )


def _drop_misused(sentence: str) -> int:
    """A `transcript drop` command line pm-ai cannot read: the usage error, exit 2."""
    leaf = TABLE["transcript"].leaves["drop"]
    _echo(
        f"pm-ai: {sentence}\n\nusage: pm-ai transcript {_spelled('drop', leaf)}",
        file=sys.stderr,
    )
    return EXIT_USAGE


def _drop_path(given: str) -> tuple[Path, str]:
    """The absolute path a drop reads, and the note its refusals add for a relative one.

    `~` is expanded here, in whichever process runs the command — the prompt
    sends `~/x.vtt` to the daemon as typed — and a relative path is read from
    the folder pm-ai was started in (story 11d).
    """
    try:
        path = Path(given).expanduser()
    except RuntimeError as homeless:
        raise Refusal(
            f"{given} starts with `~`, and the home folder it names could not be "
            f"found ({homeless}). Nothing was written. {DROP_FORMATS}"
        ) from homeless
    if path.is_absolute():
        return path, ""
    try:
        folder = Path.cwd()
    except OSError as unreadable:
        raise Refusal(
            f"{given} is a relative path, read from the folder pm-ai was started "
            f"in, and that folder could not be read "
            f"({unreadable.strerror or type(unreadable).__name__}). Nothing was "
            f"written. {DROP_FORMATS}"
        ) from unreadable
    return folder / path, f" ({given} is relative, and {RELATIVE_PATHS}, {folder})"


@dataclass(frozen=True, slots=True)
class _DropRead:
    """The dropped file as read: its text, whether from a `.docx`, and its times (story 11e)."""

    text: str
    from_docx: bool
    modified: datetime
    created: datetime | None = None


def _read_drop(path: Path, note: str = "") -> _DropRead:
    """The dropped file's text and times, or a refusal.

    Checked in this order: there is something at the path, it is not a
    document format pm-ai refuses, it is a regular file, it is within
    `MAX_DROP_BYTES`, it can be read; then a `.docx` is read as bytes by
    `pm_ai.core.docx_text`, imported here and only here — its text, and its
    `dcterms:created` when it has one in UTC (story 11e) — and anything else
    is decoded as UTF-8. The modification time comes from the same `stat`.
    Every refusal names the absolute path, with `note` for a relative one,
    and ends with `DROP_FORMATS`.
    """
    where = f"{path}{note}"
    tail = f"Nothing was written. {DROP_FORMATS}"
    if path.is_symlink() and not path.exists():
        raise Refusal(
            f"{where} is a link to nothing (a dangling link), so there is no "
            f"transcript to read. {tail}"
        )
    if not path.exists():
        raise Refusal(f"{where} does not exist. {tail}")
    described = _NOT_A_TRANSCRIPT_FORMAT.get(path.suffix.lower())
    if described is not None:
        raise Refusal(
            f"{where} is {described}, not a transcript pm-ai reads; save the "
            f"transcript in a format pm-ai reads and drop that. {tail}"
        )
    if path.is_dir():
        raise Refusal(f"{where} is a directory, not a transcript file. {tail}")
    try:
        status = path.stat()
    except OSError as unreadable:
        reason = unreadable.strerror or type(unreadable).__name__
        raise Refusal(f"{where} could not be read ({reason}). {tail}") from unreadable
    if not stat.S_ISREG(status.st_mode):
        raise Refusal(
            f"{where} is not a regular file (a FIFO, a device or a socket), so it "
            f"is not a transcript pm-ai will read. {tail}"
        )
    if status.st_size > MAX_DROP_BYTES:
        raise Refusal(
            f"{where} is {status.st_size} bytes, over the {MAX_DROP_BYTES // (1024 * 1024)} "
            f"MiB a dropped transcript may be. {tail}"
        )
    modified = datetime.fromtimestamp(status.st_mtime, tz=timezone.utc)
    try:
        raw = path.read_bytes()
    except OSError as unreadable:
        reason = unreadable.strerror or type(unreadable).__name__
        raise Refusal(f"{where} could not be read ({reason}). {tail}") from unreadable
    if path.suffix.lower() == _DOCX:
        # Story 11d: imported when a `.docx` is dropped, so the prompt's
        # startup never loads the reader.
        from pm_ai.core.docx_text import DocxRefused, docx_created, docx_text

        try:
            text = docx_text(raw, name=str(path))
        except DocxRefused as refused:
            # The reader names the absolute path; a relative one's note follows.
            said = f"{str(refused).rstrip('.')}{note}." if note else str(refused)
            raise Refusal(f"{said} {tail}") from refused
        return _DropRead(text, True, modified, docx_created(raw))
    try:
        return _DropRead(raw.decode("utf-8"), False, modified)
    except UnicodeDecodeError as undecodable:
        raise Refusal(
            f"{where} is not UTF-8 text (byte {undecodable.start} does not decode), "
            f"so pm-ai cannot read it as a transcript. {tail}"
        ) from undecodable


def _when(start: datetime, zone: str) -> str:
    """A start in UTC, and in `display_timezone` when one is set."""
    shown = start.astimezone(timezone.utc).isoformat()
    if not zone:
        return shown
    try:
        local = start.astimezone(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        return shown
    return f"{shown} ({local.strftime('%Y-%m-%d %H:%M')} in {zone})"


def _from(source: str, estimated: bool) -> str:
    """Where a value came from, and the word `estimated` when it is an estimate."""
    what = "given as an option" if source == "option" else f"from the {source}"
    return f"estimated {what}" if estimated else what


def _clauses_sentence(clauses: Sequence[str]) -> str:
    text = "; ".join(clauses)
    return f"{text[:1].upper()}{text[1:]}."


def _field_words(
    title: DroppedFieldOutcome,
    start: DroppedFieldOutcome,
    duration: DroppedFieldOutcome,
    zone: str,
) -> list[str]:
    """Each decided field, its source, and whether it is an estimate, as clauses."""
    said = [f"title {title.value!r}, {_from(title.source, title.estimated)}"]
    said.append(f"start {_when(start.value, zone)}, {_from(start.source, start.estimated)}")
    if duration.value is None:
        said.append("length unknown")
    else:
        said.append(f"{duration.value} minutes, {_from(duration.source, duration.estimated)}")
    return said


def _participant_words(participants: Sequence[tuple[str, str | None]]) -> str:
    if not participants:
        return "no participants (no turn names a speaker)"
    return "participants " + ", ".join(
        f"{name} ({handle if handle is not None else 'not linked'})"
        for name, handle in participants
    )


def _closing_line(dropped: TranscriptDropOutcome) -> str:
    """The exit-0 sentence: the capture, the meeting, what was staged, and each value's source."""
    if dropped.bound_by == "minted":
        how = f"a new manual-only record {dropped.meeting_id}"
    else:
        how = f"the recorded meeting {dropped.meeting_id}"
    line = (
        f"{dropped.capture} is stored and ingested against {how} in "
        f"{dropped.scope}: {dropped.staged} staged for approval."
    )
    if dropped.left:
        line += (
            f" {dropped.left} left as they were, already approved, rejected or "
            f"expired."
        )
    if dropped.bound_by == "matched":
        found = (
            "found by --start" if dropped.found_by == "start" else "found by title on the same day"
        )
        line += (
            f" It joined the existing meeting {dropped.title!r} "
            f"({dropped.meeting_id}), {found}."
        )
    elif dropped.bound_by == "minted":
        line += f" It is a new meeting {dropped.meeting_id}"
        if dropped.day_unknown:
            line += (
                ": its day came only from the file's modification time, so no "
                "existing meeting was joined"
            )
        elif dropped.candidates:
            line += (
                f": {len(dropped.candidates)} meetings that day share its title "
                f"({', '.join(dropped.candidates)}), so none was joined"
            )
        line += "."
    if (
        dropped.title_field is not None
        and dropped.start_field is not None
        and dropped.duration is not None
    ):
        clauses = _field_words(
            dropped.title_field, dropped.start_field, dropped.duration, dropped.zone
        )
        line += " " + _clauses_sentence(clauses)
    line += " " + _clauses_sentence([_participant_words(dropped.participants)])
    if dropped.from_docx:
        line += " The capture is the text taken from the .docx; the .docx itself is not kept."
    return line


def _preview_text(preview: DropPreviewOutcome) -> str:
    """`--dry-run`'s report: each field, its source, the participants and the outcome."""
    lines = ["A drop would store (nothing was written):"]
    title, start, duration = preview.title, preview.start, preview.duration
    if title.value is not None:
        lines.append(f"  title: {title.value} — {_from(title.source, title.estimated)}")
    if isinstance(start.value, datetime):
        lines.append(
            f"  start: {_when(start.value, preview.zone)} — {_from(start.source, start.estimated)}"
        )
    if duration.value is not None:
        lines.append(
            f"  length: {duration.value} minutes — {_from(duration.source, duration.estimated)}"
        )
    elif duration.source != "unknown" or title.value is not None:
        # Nothing gave a length; a refused `--meeting` decided no field at all.
        lines.append("  length: unknown")
    lines.append(
        "  participants: "
        + (
            ", ".join(
                f"{name} ({handle if handle is not None else 'not linked'})"
                for name, handle in preview.participants
            )
            or "none"
        )
    )
    if preview.outcome == "join":
        found = {"start": ", found by --start", "same day": ", found by title on the same day"}
        lines.append(
            f"  outcome: join the existing meeting {preview.meeting_title!r} ({preview.meeting_id})"
            f"{found.get(preview.found_by, '')}"
        )
    elif preview.outcome == "new":
        said = "  outcome: new meeting"
        if preview.day_unknown:
            said += (
                " — its day came only from the file's modification time, so no "
                "existing meeting would be joined"
            )
        elif preview.candidates:
            said += (
                f" — {len(preview.candidates)} meetings that day share its title "
                f"({', '.join(preview.candidates)}), so none would be joined"
            )
        lines.append(said)
    else:
        lines.append(f"  outcome: refused — {preview.reason}")
    return "\n".join(lines)


def _json_field(field: DroppedFieldOutcome, *, instant: bool = False) -> dict[str, object]:
    value = field.value
    if instant and isinstance(value, datetime):
        value = value.astimezone(timezone.utc).isoformat()
    return {"value": value, "source": field.source, "estimated": field.estimated}


def _preview_json(preview: DropPreviewOutcome) -> str:
    """`--dry-run --json`: one object, a wrapper's contract (story 11e)."""
    if preview.outcome == "join":
        outcome: dict[str, object] = {
            "kind": "join",
            "meeting_id": preview.meeting_id,
            "title": preview.meeting_title,
        }
    elif preview.outcome == "new":
        outcome = {"kind": "new", "candidates": list(preview.candidates)}
    else:
        outcome = {"kind": "refused", "reason": preview.reason}
    return json.dumps(
        {
            "title": _json_field(preview.title),
            "start": _json_field(preview.start, instant=True),
            "duration_minutes": _json_field(preview.duration),
            "participants": [
                {"name": name, "handle": handle} for name, handle in preview.participants
            ],
            "outcome": outcome,
        },
        ensure_ascii=False,
    )


def _refuse_undroppable(scope: DataScope) -> None:
    """The scopes a drop cannot go to, refused by name (story 11c)."""
    if scope.kind is ScopeKind.PEOPLE:
        raise Refusal(
            f"a transcript cannot be dropped into {scope}: a 1:1's transcript is "
            f"a later slice. Drop into `personal` or `project:<id>`. Nothing was "
            f"written."
        )
    if scope.kind not in _DROPPABLE_KINDS:
        raise Refusal(
            f"a transcript cannot be dropped into {scope}, which records no "
            f"meetings. Drop into `personal` or `project:<id>`. Nothing was "
            f"written."
        )


def _file_refusal(refused: UnreadableTranscript | NothingSaid) -> Refusal:
    """A reader's refusal about the file, with the formats the command reads (story 11d)."""
    return Refusal(f"{refused} {DROP_FORMATS}")


def _transcript_drop(context: Context) -> int:
    """`pm-ai transcript drop <file> --scope <scope> [--meeting <id>] [options] [--dry-run [--json]]`.

    Stories 11c, 11d and 11e. The surface of `pm_ai.app.pipelines.drop_transcript`:
    it refuses the scopes a drop cannot go to and a path that is not a readable
    transcript file — `~` expanded here, a relative path read from the folder
    pm-ai was started in, a `.docx` read for its text and its creation time —
    then hands the text, the file's times and the options to the pipeline,
    which decides every field and asks nothing. `--dry-run` prints what a drop
    would store and writes nothing; `--json` with it prints one object.
    Every refusal the pipeline meets, a filesystem error included, is passed
    through verbatim as exit `3`, and those about the file end with
    `DROP_FORMATS`; it writes nothing before the capture, and a failure or
    Ctrl-C after it says whether the capture was kept.
    """
    scope = context.target
    if scope is None:
        raise RuntimeError(
            "no target scope reached `transcript drop`. That is a wiring fault in pm-ai."
        )
    dry_run = DRY_RUN_FLAG in context.flags
    if JSON_FLAG in context.flags and not dry_run:
        return _drop_misused(
            f"`--{JSON_FLAG}` prints `--{DRY_RUN_FLAG}`'s preview as one object, so it "
            f"is given only with `--{DRY_RUN_FLAG}`."
        )
    try:
        given = _given(context.options)
    except _BadAnswer as bad:
        return _drop_misused(str(bad))
    _refuse_undroppable(scope)
    (file,) = context.arguments
    path, note = _drop_path(file)
    read = _read_drop(path, note)
    context.require_daemon()
    drop = context.transcript_drop
    if drop is None:
        raise Refusal(
            "no transcript pipeline was supplied to the CLI, so nothing was "
            "dropped. That is a wiring fault in pm-ai, not this machine's."
        )
    try:
        dropped = drop(
            text=read.text,
            file_name=str(path),
            scope=scope,
            meeting_id=context.options.get(MEETING_ARGUMENT),
            from_docx=read.from_docx,
            modified=read.modified,
            created=read.created,
            title=given.title,
            start=given.start,
            duration=given.duration,
            attendees=given.attendees or (),
            dry_run=dry_run,
        )
    except (UnreadableTranscript, NothingSaid) as refused:
        raise _file_refusal(refused) from refused
    except (
        UnboundTranscript,
        MeetingNotFound,
        AmbiguousMeeting,
        MalformedMeeting,
        CaptureAlreadyExists,
        UnprotectedCaptureDir,
        EmptyCapture,
        MalformedCaptureName,
        CaptureNotIngested,
        DropInterrupted,
        DropRefused,
        ScopeResolutionError,
        ArtifactBusy,
    ) as refused:
        message = refused.args[0] if isinstance(refused, KeyError) and refused.args else refused
        raise Refusal(str(message)) from refused
    except OSError as failed:
        # The filesystem's own refusal, at the listing, the candidate lookup or
        # the capture write — all before anything is written, or the capture
        # write itself, which publishes whole or not at all.
        raise Refusal(f"the drop stopped and nothing was written: {failed}") from failed
    for notice in dropped.notices:
        _echo(f"pm-ai: {notice}", file=sys.stderr)
    if isinstance(dropped, DropPreviewOutcome):
        _echo(_preview_json(dropped) if JSON_FLAG in context.flags else _preview_text(dropped))
        return EXIT_OK
    _echo(_closing_line(dropped))
    return EXIT_OK


LOG_LINES_OPTION = "lines"
LOG_LEVEL_OPTION = "level"
LOG_FOLLOW_FLAG = "follow"


def _logs(context: Context) -> int:
    """`pm-ai logs [--lines <n>] [--level <level>] [--follow]` (story 4w).

    The daemon's log, read from its files in this process: the last `n` lines
    at that level or above (default 50, every level), in this machine's local
    zone, then — with `--follow` — every new line until Ctrl-C. The first line,
    on standard error, names the file and the zone; standard output carries
    only log lines. Never through the daemon: `logs` is `TERMINAL_BOUND`.
    """
    count = lines_value(context.options.get(LOG_LINES_OPTION))
    if count is None:
        return _logs_misused(
            f"`--{LOG_LINES_OPTION}` takes a whole number from 1 to {LOG_MAX_LINES} in "
            f"the digits 0-9, not {context.options[LOG_LINES_OPTION]!r}"
        )
    level = level_value(context.options.get(LOG_LEVEL_OPTION))
    if level is None:
        return _logs_misused(
            f"`--{LOG_LEVEL_OPTION}` takes one of {', '.join(LOG_LEVELS)}, not "
            f"{context.options[LOG_LEVEL_OPTION]!r}"
        )
    if context.daemon_log is None:
        raise Refusal(
            "no daemon log location was supplied to the CLI, so nothing was read. "
            "This is a wiring fault in pm-ai, not this machine's."
        )
    # The process's own stdout is written unbuffered, so a pipe closed early
    # leaves nothing for the interpreter's exit-time flush to fail on.
    out = _OUT.get() or unbuffered_stream(sys.stdout)
    err = _ERR.get() or sys.stderr
    try:
        return show_log(
            context.daemon_log,
            lines=count,
            level=level,
            follow=LOG_FOLLOW_FLAG in context.flags,
            out=out,
            err=err,
        )
    except LogUnreadable as unreadable:
        raise Refusal(str(unreadable)) from unreadable


def _logs_misused(sentence: str) -> int:
    """A `logs` value pm-ai cannot read: the usage error, exit 2, on this command's error stream."""
    _echo(
        f"pm-ai: {sentence}\n\nusage: pm-ai {_spelled('logs', TABLE['logs'])}",
        file=sys.stderr,
    )
    return EXIT_USAGE


RESTART_SENTENCE = (
    "A daemon already running sees this after it is restarted (`pm-ai daemon run`)."
)
"""The one sentence every command whose change waits for a daemon restart says (AD-5).

Owned here, beside the table, by story 4r: `project add` and `key enrol` print
it every time they succeed, wherever they run, and the daemon names a connector
row filing into a project enrolled since it started with it. Later slices print
this constant, never a sentence of their own.
"""

def _within(span: timedelta) -> str:
    """A span of whole minutes as the sentences say it: `a minute`, `240 minutes`."""
    minutes = int(span.total_seconds() // 60)
    return "a minute" if minutes == 1 else f"{minutes} minutes"


HARVESTED_WHEN = (
    f"A running daemon needs no restart: it starts harvesting a new connector "
    f"the next time it reads the connector list — normally within "
    f"{_within(FIRST_RETRY)} when no other connector is set up, otherwise at "
    f"the next scheduled harvest of any connector, normally within "
    f"{_within(CYCLE)}. A daemon started afterwards normally harvests it within "
    f"its first {_within(START_SPREAD)}."
)
"""When a newly enrolled connector is first harvested (story 4s), said by both enrolments.

Read off the harvest timers' own constants (`pm_ai.core.cadence`): the list is
read every `FIRST_RETRY` while no listed connector is set up and at least once
per `CYCLE` otherwise, and the first harvests after a start are spread over
`START_SPREAD`. "Normally": a harvest already running when the list is due
delays the read until it ends.
"""

PROJECT_CLAUSE = (
    "{instance} files into {projects}, so a running daemon that started before "
    "{which} was enrolled does not build it — and so does not harvest it — until "
    "it is restarted."
)
"""Said, then `RESTART_SENTENCE`, whenever a new or saved row files into a project (4s).

A daemon composes projects at start only (AD-5), and a Graph row mapped to a
project it did not load is not built at all. The CLI cannot see which projects
a running daemon loaded, so the clause is said whenever the row names one.
"""

# ── `pm-ai transcript summarize` (story 7b) ──────────────────────────────────

BY_OPTION = "by"
"""`transcript summarize`'s `--by local|claude`: who makes the summary."""

SUMMARY_MAKERS = ("local", "claude")
"""What `--by` takes. `local` is the default; `claude` asks Claude in the daemon (7c)."""

SUMMARIZE_WORDS = ("transcript", "summarize")

def _summarize_target(options: Mapping[str, str]) -> DataScope:
    """`--scope`'s scope, required; `_Misread` when absent or naming none."""
    requested = options.get(SCOPE_ARGUMENT)
    scope = None if requested is None else _scope(requested)
    if scope is None:
        raise _Misread(
            f"`transcript summarize` needs `--{SCOPE_ARGUMENT} personal` or "
            f"`--{SCOPE_ARGUMENT} project:<id>` naming the meeting's scope"
            + ("" if requested is None else f"; `{requested}` is not a scope")
            + "."
        )
    by = options.get(BY_OPTION)
    if by is not None and by not in SUMMARY_MAKERS:
        raise _Misread(
            f"`--{BY_OPTION}` takes {' or '.join(SUMMARY_MAKERS)}; `{by}` is neither."
        )
    return scope


class SummaryRequestedOutcome(Protocol):
    """What a summary request said — `pm_ai.app.pipelines.SummaryRequested`, by shape."""

    @property
    def line(self) -> str: ...


class TranscriptSummarize(Protocol):
    """`pm-ai transcript summarize`'s request, bound to the daemon's job (story 7b).

    `pm_ai.app.pipelines.request_summary` is the real one, and only the daemon
    binds it: typed at a terminal, the line is sent to the daemon instead.
    """

    def __call__(self, *, scope: DataScope, meeting_id: str, by: str) -> SummaryRequestedOutcome: ...


def _transcript_summarize(context: Context) -> int:
    """`pm-ai transcript summarize --scope <scope> --meeting <id> [--by local|claude]` (7b, 7c).

    Only the daemon summarises, by the local model or, with `--by claude`, by
    Claude (story 7c) — the one frontier call pm-ai makes, and only when asked.
    In the daemon this asks for the summary and the job's result follows when
    the model has answered; anywhere else no pipeline is bound and the command
    is refused, naming `pm-ai daemon run`.
    """
    scope = context.target
    if scope is None:
        raise RuntimeError(
            "no target scope reached `transcript summarize`. That is a wiring fault in pm-ai."
        )
    if scope.kind not in _DROPPABLE_KINDS:
        raise Refusal(
            f"a meeting in {scope} cannot be summarised here: summaries are made "
            f"for meetings in `personal` or `project:<id>`. Nothing was sent."
        )
    summarize = context.transcript_summarize
    if summarize is None:
        raise Refusal(
            "only the daemon summarises a transcript, and no daemon took this "
            "line; start one with `pm-ai daemon run` and ask again. Nothing was "
            "sent."
        )
    try:
        requested = summarize(
            scope=scope,
            meeting_id=context.options[MEETING_ARGUMENT],
            by=context.options.get(BY_OPTION, SUMMARY_MAKERS[0]),
        )
    except (SummaryRefused, MeetingNotFound, MalformedMeeting, ArtifactBusy) as refusal:
        message = refusal.args[0] if isinstance(refusal, KeyError) and refusal.args else refusal
        raise Refusal(str(message)) from refusal
    _echo(requested.line)
    return EXIT_OK


# ── `pm-ai frontier key` (story 7c) ──────────────────────────────────────────

FRONTIER_KEY_WORDS = ("frontier", "key")


class FrontierKeyHand(Protocol):
    """`pm_ai.surfaces.cli.loopback.LoopbackClient`'s two calls `frontier key` makes."""

    def reachable(self) -> object | None: ...

    def hand_frontier_key(self, key: str) -> object: ...


def _frontier_key(context: Context) -> int:
    """`pm-ai frontier key`: read the Anthropic API key at a hidden prompt, hand it to the daemon (7c).

    Never on the command line: the key is read here, without echo, and sent
    over the authenticated loopback API to the running daemon, which checks it
    with Anthropic (listing models, which bills nothing) and seals it under the
    master key. Every request to Anthropic leaves from the daemon; this process
    makes none. No daemon: refused, naming `pm-ai daemon run`, before the key
    is asked for. A refused key, or Anthropic not answering, seals nothing, and
    a key already held stays.
    """
    if not _stdin_is_terminal():
        raise Refusal(
            "`pm-ai frontier key` reads the key at a hidden prompt, so it needs a "
            "terminal. stdin is not a TTY here, and a key typed into a pipe or "
            "given on a command line would be left in history. Nothing was asked "
            "and nothing was sealed. Run it from an interactive shell."
        )
    hand = context.frontier_key
    if hand is None:
        raise Refusal(
            "only the running daemon checks and seals the key, and none is reachable "
            "from here; start one with `pm-ai daemon run` and run "
            "`pm-ai frontier key` again. Nothing was asked."
        )
    unreachable = hand.reachable()
    if unreachable is not None:
        raise Refusal(
            f"{getattr(unreachable, 'sentence', 'the daemon could not be reached')} "
            f"The key is handed to the running daemon (`pm-ai daemon run`), so it "
            f"was not asked for."
        )
    key = _hidden("Anthropic API key (not shown as you type): ")
    if not key:
        raise Refusal("no key was typed, so nothing was sealed.")
    with _interruptible():
        answer = hand.hand_frontier_key(key)
    output = getattr(answer, "output", None)
    code = getattr(answer, "code", None)
    if isinstance(output, str) and isinstance(code, int):
        _echo(output, end="", file=sys.stdout if code == EXIT_OK else sys.stderr)
        return code
    raise Refusal(
        f"{getattr(answer, 'sentence', 'the daemon gave no answer this client knows')} "
        f"Whether the key was sealed is not known; `pm-ai doctor` says."
    )


# ── `pm-ai member` (story 7e) ────────────────────────────────────────────────

REMOVE_FLAG = "remove"
CONFIRM_FLAG = "confirm"
REJECT_FLAG = "reject"
LABEL_ARGUMENT = "label"
MEMBER_ARGUMENT = "member"
UPDATE_ARGUMENT = "update"


class Members(Protocol):
    """What the `member` commands reach: `pm_ai.app.members.MemberDesk`, by shape.

    Each refusal is a `MemberRefused` carrying the sentence the PM reads.
    """

    def plan_name(
        self,
        scope: DataScope,
        label: str,
        member: str | None,
        *,
        meeting: str | None,
        remove: bool,
    ) -> NamePlan: ...

    def save_name(self, plan: NamePlan) -> str: ...

    def link(self, scope: DataScope, update: str, *, confirm: bool) -> str: ...

    def unmatched(self, scope: DataScope) -> str: ...


def _member_target(options: Mapping[str, str]) -> DataScope:
    """`--scope`'s scope, required; `_Misread` when absent or naming none.

    A scope that is not a project parses, and the command refuses it by name:
    meeting updates are kept per project.
    """
    requested = options.get(SCOPE_ARGUMENT)
    scope = None if requested is None else _scope(requested)
    if scope is None:
        raise _Misread(
            f"the `member` commands need `--{SCOPE_ARGUMENT} project:<id>` naming an "
            f"enrolled project"
            + ("" if requested is None else f"; `{requested}` is not a scope")
            + "."
        )
    return scope


def _member_desk(context: Context) -> tuple[Members, DataScope]:
    scope = context.target
    if scope is None:
        raise RuntimeError("no target scope reached a `member` command. That is a wiring fault in pm-ai.")
    if scope.kind is not ScopeKind.PROJECT:
        raise Refusal(
            f"meeting updates are kept per project, and {scope} is not one: give "
            f"`--{SCOPE_ARGUMENT} project:<id>`. Nothing was done."
        )
    context.require_daemon()
    if context.members is None:
        raise Refusal("the meeting updates could not be reached here; nothing was done.")
    return context.members, scope


def _member_name(context: Context) -> int:
    """`pm-ai member name --scope project:<id> "<label>" <member> [--meeting <id>] [--remove]` (7e).

    Saves a name the PM confirmed as a member's `gitlab:<host>:<id>` handle in
    `aliases.md`. A label already naming someone else is replaced only after
    a `(y/n)` at the terminal naming both; `--remove` appends a removal. A
    generic label is named only for one meeting (`--meeting`).
    """
    members, scope = _member_desk(context)
    label = context.arguments[0]
    member = context.arguments[1] if len(context.arguments) > 1 else None
    remove = REMOVE_FLAG in context.flags
    if remove and member is not None:
        raise Refusal(
            f"`member name --{REMOVE_FLAG}` takes the label alone, not a member; nothing was written."
        )
    try:
        plan = members.plan_name(
            scope, label, member, meeting=context.options.get(MEETING_ARGUMENT), remove=remove
        )
        if plan.row is not None and plan.replaces is not None:
            if not _stdin_is_terminal():
                raise Refusal(
                    f"the label is named as {plan.replaces} now, and replacing it is asked "
                    f"at a terminal; stdin is not one here. Nothing was written."
                )
            _echo(f"the label is named as {plan.replaces} now.")
            if not _confirmed(f"name it as {plan.names} instead? (y/n): "):
                raise Refusal("the label was not renamed; nothing was written.")
        said = members.save_name(plan)
    except MemberRefused as refused:
        raise Refusal(str(refused)) from refused
    _echo(said)
    return EXIT_OK


def _member_link(context: Context) -> int:
    """`pm-ai member link --scope project:<id> <update id> --confirm|--reject` (7e)."""
    members, scope = _member_desk(context)
    confirm = CONFIRM_FLAG in context.flags
    reject = REJECT_FLAG in context.flags
    if confirm == reject:
        raise Refusal(
            f"`member link` takes exactly one of `--{CONFIRM_FLAG}` and `--{REJECT_FLAG}`; "
            f"nothing was written."
        )
    try:
        said = members.link(scope, context.arguments[0], confirm=confirm)
    except MemberRefused as refused:
        raise Refusal(str(refused)) from refused
    _echo(said)
    return EXIT_OK


def _member_unmatched(context: Context) -> int:
    """`pm-ai member unmatched --scope project:<id>` (7e): labels, links, skipped lines."""
    members, scope = _member_desk(context)
    try:
        report = members.unmatched(scope)
    except MemberRefused as refused:
        raise Refusal(str(refused)) from refused
    _echo(report, end="")
    return EXIT_OK


TERMINAL_BOUND: frozenset[tuple[str, ...]] = frozenset(
    {
        ("goal", "set"),
        ("goal", "update"),
        ("goal", "delete"),
        ("setup",),
        ("connector", "add"),
        ("connector", "sign-in"),
        ("connector", "categories"),
        ("key", "enrol"),
        # Story 7c: the key is read at a hidden prompt in the client's terminal.
        ("frontier", "key"),
        ("logs",),
        # Story 7e: replacing a label's member is confirmed at the terminal.
        ("member", "name"),
    }
)
"""The commands that need this terminal: they ask questions, read a secret, or
hold the terminal until Ctrl-C.

A line sent through the daemon naming one of these is refused by name (story
4r, `4q`'s routing rule) — the daemon's terminal is not the client's. `logs`
(story 4w) is here rather than given a bound: `--follow` has no end, and it
would hold one of the daemon's four workers until the client went away.
"""

TAKES_A_PATH: frozenset[tuple[str, ...]] = frozenset(
    {("project", "add"), ("transcript", "drop")}
)
"""The commands whose first positional argument is a filesystem path.

The daemon refuses a relative one (story 4r) — it does not run in the
directory the line was typed in — and the prompt makes it absolute against its
own working directory before sending (story 4q), so the two read one list.
"""

TABLE: Mapping[str, Command] = {
    "dashboard": Command(
        "render this scope's daily_dashboard.md, once",
        _dashboard,
        options=(SCOPE_ARGUMENT,),
        target=_dashboard_target,
    ),
    "doctor": Command("check this machine and report what is wrong with it", _doctor),
    # `4w`: the daemon's log, read from its files in this process — no daemon,
    # keychain or settings needed.
    "logs": Command(
        "print the daemon log's recent lines in local time; --follow keeps printing",
        _logs,
        options=(LOG_LINES_OPTION, LOG_LEVEL_OPTION),
        flags=(LOG_FOLLOW_FLAG,),
        composes=False,
        reads_daemon_log=True,
    ),
    # `4e`: the long-lived daemon. Foreground only — starting it at login under
    # launchd is a later slice, and the help says so.
    "daemon": Command(
        "the long-lived daemon; foreground only, starting at login is a later slice",
        leaves={
            "run": Command(
                "serve on 127.0.0.1:<daemon_port> (default 8747) in the "
                "foreground until Ctrl-C",
                _daemon_run,
                target=_application,
            ),
        },
    ),
    # `4h`: the one command that walks a new machine through the key, a
    # project and `config.toml` — and, since `4p`, offers a Graph connector.
    "setup": Command(
        "first run: enrol the key, register a project, write config.toml, "
        "optionally add a Microsoft Graph connector, then probe",
        _setup,
    ),
    # The three groups, each with the one leaf `4j` hung on it. They were in the
    # table from `4c` with no leaves, so the shape of the CLI was settled in one
    # place and this slice added mappings rather than inventing a second table.
    "key": Command(
        "manage the master key pm-ai seals artifacts with",
        leaves={
            "enrol": Command(
                "mint the master key and store it in the keychain",
                _key_enrol,
                target=_application,
            ),
        },
    ),
    # `7c`: the Anthropic API key, handed to the running daemon and sealed in
    # the encrypted credential file — not a keychain item, so not under `key`.
    "frontier": Command(
        "the Anthropic API key `transcript summarize --by claude` uses",
        leaves={
            "key": Command(
                "type the Anthropic API key at a hidden prompt; the running daemon "
                "checks it with Anthropic and seals it in config.json",
                _frontier_key,
                composes=False,
            ),
        },
    ),
    "config": Command(
        "inspect ~/.pm-ai/config.toml",
        leaves={
            "show": Command(
                "print every setting, marked set or default",
                _config_show,
                target=_application,
            ),
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
            "set": Command(
                "create or revise one goal in strategic_goals.md",
                _goal_set,
                optional=("id",),
                options=GOAL_OPTIONS,
                target=_goals_target,
            ),
            "update": Command(
                "revise one goal on file; never creates one",
                _goal_update,
                takes=("id",),
                options=GOAL_OPTIONS,
                target=_goals_target,
            ),
            "delete": Command(
                "remove one goal and its detail lines, asking first",
                _goal_delete,
                takes=("id",),
                flags=(YES_FLAG,),
                target=_goals_target,
            ),
            "list": Command(
                "list the goals on file, by domain",
                _goal_list,
                target=_goals_target,
                composes=False,
            ),
            "show": Command(
                "show one goal, its citation and its file",
                _goal_show,
                takes=("id",),
                target=_goals_target,
                composes=False,
            ),
        },
    ),
    # `11c`: a transcript somebody downloaded or typed, ingested by hand.
    "transcript": Command(
        "meeting transcripts pm-ai extracts from",
        leaves={
            "drop": Command(
                "ingest a .vtt, .txt, .md or .docx transcript against a meeting in "
                "that scope; a relative <file> is read from the folder pm-ai was "
                "started in",
                _transcript_drop,
                takes=("file",),
                options=(SCOPE_ARGUMENT, MEETING_ARGUMENT, *DROP_ANSWERS),
                flags=DROP_FLAGS,
                requires=(SCOPE_ARGUMENT,),
                target=_drop_target,
            ),
            # `7b`: a meeting's summary, decisions and action items, made by the
            # local model in the daemon.
            "summarize": Command(
                "ask the daemon's local model (or, with --by claude, Claude) for a "
                "meeting's summary, decisions and action items; the daemon must be "
                "running",
                _transcript_summarize,
                options=(SCOPE_ARGUMENT, MEETING_ARGUMENT, BY_OPTION),
                requires=(SCOPE_ARGUMENT, MEETING_ARGUMENT),
                target=_summarize_target,
                via_daemon=True,
            ),
        },
    ),
    # `7e`: meeting updates on the project's GitLab issues, and the saved
    # names they are attributed by.
    "member": Command(
        "meeting updates on a project's GitLab issues, and who a speaker label names",
        leaves={
            "name": Command(
                "save that a speaker label names a project member (aliases.md); "
                "--remove ends it, --meeting limits it to one meeting",
                _member_name,
                takes=(LABEL_ARGUMENT,),
                optional=(MEMBER_ARGUMENT,),
                options=(SCOPE_ARGUMENT, MEETING_ARGUMENT),
                requires=(SCOPE_ARGUMENT,),
                flags=(REMOVE_FLAG,),
                target=_member_target,
            ),
            "link": Command(
                "confirm or reject the issue a meeting update is linked to",
                _member_link,
                takes=(UPDATE_ARGUMENT,),
                options=(SCOPE_ARGUMENT,),
                requires=(SCOPE_ARGUMENT,),
                flags=(CONFIRM_FLAG, REJECT_FLAG),
                target=_member_target,
            ),
            "unmatched": Command(
                "list speaker labels matched to no member, links awaiting your word, "
                "and lines skipped",
                _member_unmatched,
                options=(SCOPE_ARGUMENT,),
                requires=(SCOPE_ARGUMENT,),
                target=_member_target,
            ),
        },
    ),
    "connector": Command(
        "the connectors this daemon harvests from",
        leaves={
            "add": Command(
                "enrol a connector: probe the credential, then seal it",
                _connector_add,
                takes=("system", "instance"),
                target=_application,
            ),
            "check": Command(
                "probe every connector, within 10s in total",
                _connector_check,
                target=_application,
            ),
            "sign-in": Command(
                "redo the Microsoft sign-in for an enrolled Graph connector",
                _connector_sign_in,
                takes=("instance",),
                target=_application,
            ),
            # `4s`: which Outlook category files a meeting into which project.
            "categories": Command(
                "map Outlook categories to projects for an enrolled Graph connector",
                _connector_categories,
                takes=("instance",),
                target=_application,
            ),
        },
    ),
}

_HELP_FLAGS = frozenset({"-h", "--help", "help"})

_SUBCOMMAND_HELP = frozenset({"-h", "--help"})
"""What asks for one subcommand's help after its name (story 4t). Not the bare
word `help`, which could be a positional — a goal id, say."""


def names_a_command(word: str) -> bool:
    """Whether `word`, as a first word, names something `parse` runs or answers.

    A command in `TABLE`, or a help word. The prompt (story 4f) asks this to
    tell a command from an open line, so there is one table and no copy.
    """
    return word in TABLE or word in _HELP_FLAGS


def _spelled(name: str, command: Command) -> str:
    """One command's name with what it takes, as an operator would type it.

    Required positionals in angle brackets and optional options in square ones,
    which is the convention every other CLI on the machine uses. Composed from
    `takes` and `options` rather than written out beside the summary, so a row
    that gains an argument cannot keep an old usage line.
    """
    required = "".join(f" <{argument}>" for argument in command.takes)
    positional = "".join(f" [{argument}]" for argument in command.optional)
    optional = "".join(
        f" --{option} <{option}>" if option in command.requires else f" [--{option} <{option}>]"
        for option in command.options
    )
    flagged = "".join(f" [--{flag}]" for flag in command.flags)
    return f"{name}{required}{positional}{optional}{flagged}"


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


@dataclass(frozen=True, slots=True)
class Parsed:
    """A command line `parse` read: what to run, with what, and where it acts.

    The one reading of `argv` (AD-11: "the command line is parsed once"). The
    composition root reads `target` to decide where the daemon acts *before*
    anything about projects is decided, and `dispatch` runs this value rather
    than parsing `argv` a second time.
    """

    run: Callable[[Context], int]
    arguments: tuple[str, ...] = ()
    options: Mapping[str, str] = field(default_factory=dict)
    target: DataScope | None = None
    """The command's target; `None` for a command that needs no daemon."""

    flags: frozenset[str] = frozenset()
    """The value-less flags given (story 4t)."""

    composes: bool = True
    """`Command.composes`, carried so the composition root can skip composing."""

    reads_daemon_log: bool = False
    """`Command.reads_daemon_log`, carried so `logs` is answered before the keychain."""

    via_daemon: bool = False
    """`Command.via_daemon`, carried so a terminal sends the line to the daemon (7b)."""


@dataclass(frozen=True, slots=True)
class Answered:
    """A command line `parse` answered on its own: help, or a usage error.

    Nothing is composed for one of these. Returned rather than printed so
    `parse` stays a pure step; `deliver` prints it and gives the exit code.
    """

    code: int
    text: str

    def deliver(self) -> int:
        _echo(self.text, file=sys.stdout if self.code == EXIT_OK else sys.stderr)
        return self.code


def _misused(sentence: str, *, group: str | None = None) -> Answered:
    return Answered(EXIT_USAGE, f"pm-ai: {sentence}\n\n{usage(group=group)}")


def parse(argv: Sequence[str]) -> Parsed | Answered:
    """Read `argv` once: the command, its words, and the scope it targets.

    Pure — nothing is printed, opened or composed — so `pm_ai.app.entry.main`
    can call it first and compose a daemon for the target it names (story 4m).
    A usage error is answered here, before any project decision: `--scope
    alpha` exits `2` on a machine where nothing could be composed at all.

    Hand-rolled rather than `argparse`: the surface is a table of at most two
    words, and `argparse` answers `--help` by raising `SystemExit` from inside
    the parse — which is exactly the control flow an explicitly-passed `argv`
    exists to avoid. (`pm_ai.app.entry` catches `SystemExit` anyway, because a
    library below here may still raise one.)
    """
    if not argv:
        # Always a usage error here. Opening the prompt for a bare call at a
        # terminal (`4f`) is routed in `pm_ai.app.entry.main`, before this is
        # called, and is no property of `parse`. A bare call that silently
        # succeeded is how a broken install reads as a working one.
        return Answered(EXIT_USAGE, usage())
    name, *rest = argv
    if name in _HELP_FLAGS:
        if rest:
            # Every other path refuses trailing words; this one dropped them and
            # exited 0, so `pm-ai --help enrol` looked like it had answered a
            # question about `enrol` while printing the top-level usage.
            return _misused(f"`{name}` takes no arguments")
        return Answered(EXIT_OK, usage())
    command = TABLE.get(name)
    if command is None:
        return _misused(f"unknown command {name!r}")
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
        if rest and not command.options and not command.flags:
            return _misused(f"`{name}` takes no arguments")
        # `23b`'s mechanism, and it narrows rather than relaxes the rule above:
        # a command declaring no options still refuses every trailing word, and
        # one that declares some refuses every word that is not one of them. The
        # flag an operator invented to be careful with must never be the thing
        # that silently vanishes.
        named, given, misread = _options(rest, allowed=command.options, flags=command.flags)
        if misread is not None:
            return _misused(f"`{name}` {misread}")
        absent = _missing(command, named)
        if absent is not None:
            return _misused(f"`{name}` {absent}")
        return _parsed(command, command.run, options=named, flags=given)
    leaf = command.leaves.get(rest[0]) if rest else None
    if leaf is None or leaf.run is None:
        return Answered(EXIT_USAGE, usage(group=name))
    if len(rest) == 2 and rest[1] in _SUBCOMMAND_HELP:
        # Story 4t: one subcommand's own usage line and summary. `11d` relies
        # on it; before, `--help` here was an option nobody declared.
        return Answered(
            EXIT_OK, f"usage: pm-ai {name} {_spelled(rest[0], leaf)}\n\n{leaf.summary}"
        )
    supplied = tuple(rest[1:])
    leaf_options: Mapping[str, str] = {}
    leaf_flags: frozenset[str] = frozenset()
    if leaf.options or leaf.flags:
        # Story 11c: a leaf with options as well as positionals. The words are
        # split first — every `--name` with its value, the rest in order — so
        # `drop <file> --scope personal` and `drop --scope personal <file>`
        # read alike, and each half meets the refusal it always met.
        supplied, option_words = _split_options(supplied, flags=leaf.flags)
        leaf_options, leaf_flags, misread = _options(
            option_words, allowed=leaf.options, flags=leaf.flags
        )
        if misread is not None:
            return _misused(f"`{name} {rest[0]}` {misread}", group=name)
        absent = _missing(leaf, leaf_options)
        if absent is not None:
            return _misused(f"`{name} {rest[0]}` {absent}", group=name)
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
        hint = (
            "; a path with spaces must be in quotes, as 'My Notes.vtt'"
            if (name, rest[0]) == DROP_WORDS and len(supplied) > len(leaf.takes)
            else ""
        )
        return _misused(f"`{name} {rest[0]}` takes {expected}{hint}", group=name)
    return _parsed(
        leaf, leaf.run, arguments=supplied, options=leaf_options, flags=leaf_flags, group=name
    )


def _parsed(
    command: Command,
    run: Callable[[Context], int],
    *,
    arguments: tuple[str, ...] = (),
    options: Mapping[str, str] | None = None,
    flags: frozenset[str] = frozenset(),
    group: str | None = None,
) -> Parsed | Answered:
    """The parsed command with its target read, or the usage error naming why not."""
    named = {} if options is None else options
    try:
        target = None if command.target is None else command.target(named)
    except _Misread as misread:
        return _misused(str(misread), group=group)
    return Parsed(
        run,
        arguments=arguments,
        options=named,
        target=target,
        flags=flags,
        composes=command.composes,
        reads_daemon_log=command.reads_daemon_log,
        via_daemon=command.via_daemon,
    )


def dispatch(
    argv: Sequence[str] | Parsed,
    *,
    daemon: DaemonPort | None,
    diagnose: Callable[[], HealthReport],
    probe_connectors: Callable[[], Report] | None,
    probe_credential: CredentialProbePort = _no_probe,
    graph_sign_in: GraphSignIn | None = None,
    gitlab_enrolment: GitLabCheck | None = None,
    onboard: Callable[[str, str | None], OnboardOutcome] = _no_onboarding,
    dashboard: Callable[[DataScope], Path] = _no_dashboard,
    first_run: FirstRun | None = None,
    goals: GoalBook | None = None,
    goal_reader: GoalReader | None = None,
    daemon_log: DaemonLog | None = None,
    transcript_drop: TranscriptDrop | None = None,
    transcript_summarize: TranscriptSummarize | None = None,
    members: Members | None = None,
    frontier_key: FrontierKeyHand | None = None,
    serve: Callable[[], int] | None = None,
    unavailable: str | None = None,
    undecided: str | None = None,
    unbuilt_connectors: tuple[UnbuiltConnector, ...] = (),
    out: TextIO | None = None,
    err: TextIO | None = None,
    terminal: bool | None = None,
) -> int:
    """Run an already-parsed command, and return the exit code the table gives it.

    `pm_ai.app.entry.main` passes the `Parsed` it composed for, so the command
    line is read once (story 4m). An argument vector — *without* the program
    name — is still accepted and parsed here, so every row of the CLI's matrix
    stays a unit test that needs neither a subprocess nor a composition root.
    """
    streams = (_OUT.set(out), _ERR.set(err), _TERMINAL.set(terminal))
    try:
        return _dispatch(
            argv,
            daemon=daemon,
            diagnose=diagnose,
            probe_connectors=probe_connectors,
            probe_credential=probe_credential,
            graph_sign_in=graph_sign_in,
            gitlab_enrolment=gitlab_enrolment,
            onboard=onboard,
            dashboard=dashboard,
            first_run=first_run,
            goals=goals,
            goal_reader=goal_reader,
            daemon_log=daemon_log,
            transcript_drop=transcript_drop,
            transcript_summarize=transcript_summarize,
            members=members,
            frontier_key=frontier_key,
            serve=serve,
            unavailable=unavailable,
            undecided=undecided,
            unbuilt_connectors=unbuilt_connectors,
            out=out,
            err=err,
            terminal=terminal,
        )
    finally:
        _TERMINAL.reset(streams[2])
        _ERR.reset(streams[1])
        _OUT.reset(streams[0])


def _dispatch(
    argv: Sequence[str] | Parsed,
    *,
    daemon: DaemonPort | None,
    diagnose: Callable[[], HealthReport],
    probe_connectors: Callable[[], Report] | None,
    probe_credential: CredentialProbePort,
    graph_sign_in: GraphSignIn | None,
    gitlab_enrolment: GitLabCheck | None,
    onboard: Callable[[str, str | None], OnboardOutcome],
    dashboard: Callable[[DataScope], Path],
    first_run: FirstRun | None,
    goals: GoalBook | None,
    goal_reader: GoalReader | None,
    daemon_log: DaemonLog | None,
    transcript_drop: TranscriptDrop | None,
    transcript_summarize: TranscriptSummarize | None,
    members: Members | None,
    frontier_key: FrontierKeyHand | None,
    serve: Callable[[], int] | None,
    unavailable: str | None,
    undecided: str | None,
    unbuilt_connectors: tuple[UnbuiltConnector, ...],
    out: TextIO | None,
    err: TextIO | None,
    terminal: bool | None,
) -> int:
    parsed = argv if isinstance(argv, Parsed) else parse(argv)
    if isinstance(parsed, Answered):
        return parsed.deliver()
    context = Context(
        daemon=daemon,
        diagnose=diagnose,
        probe_connectors=probe_connectors,
        probe_credential=probe_credential,
        graph_sign_in=graph_sign_in,
        gitlab_enrolment=gitlab_enrolment,
        onboard=onboard,
        dashboard=dashboard,
        first_run=first_run,
        goals=goals,
        goal_reader=goal_reader,
        daemon_log=daemon_log,
        transcript_drop=transcript_drop,
        transcript_summarize=transcript_summarize,
        members=members,
        frontier_key=frontier_key,
        serve=serve,
        unavailable=unavailable,
        undecided=undecided,
        unbuilt_connectors=unbuilt_connectors,
        arguments=parsed.arguments,
        options=parsed.options,
        flags=parsed.flags,
        target=parsed.target,
        out=out,
        err=err,
        terminal=terminal,
    )
    return _run(parsed.run, context)


def _options(
    words: Sequence[str], *, allowed: tuple[str, ...], flags: tuple[str, ...] = ()
) -> tuple[Mapping[str, str], frozenset[str], str | None]:
    """`--name value` and `--name=value` pairs and bare `--flag`s, or the sentence refusing them.

    Hand-rolled for `dispatch`'s own reason: `argparse` answers a parse error by
    raising `SystemExit` from inside the parse, which is the control flow an
    explicitly-passed `argv` exists to avoid.

    A refusal is returned rather than raised because it is a *usage* error, and
    usage errors exit `2` while `Refusal` exits `3`. Each says which word was
    the problem: a bare positional, an option nobody declared, one given twice,
    one with nothing after it, and a flag given a value (story 4t).
    """
    parsed: dict[str, str] = {}
    given: set[str] = set()
    remaining = list(words)
    while remaining:
        word = remaining.pop(0)
        if not word.startswith("--"):
            return {}, frozenset(), f"takes options, not `{word}`"
        option, assigned, inline = word[2:].partition("=")
        if option in flags:
            if assigned:
                return {}, frozenset(), f"was given `{word}`; `--{option}` takes no value"
            if option in given:
                return {}, frozenset(), f"was given `--{option}` twice"
            given.add(option)
            continue
        if option not in allowed:
            spelled = ", ".join(f"--{name}" for name in (*allowed, *flags))
            return {}, frozenset(), f"has no `--{option}` option; it takes {spelled}"
        if option in parsed:
            # Not "last one wins": two values for one option is an operator who
            # believes something other than what would happen, and picking one
            # silently is how they go on believing it.
            return {}, frozenset(), f"was given `--{option}` twice"
        if assigned:
            if not inline:
                # `--scope=` is an operator who meant to type a value and did
                # not. Passed through, it reaches the handler as `""` and is
                # refused as "not a scope" — a message about a word nobody
                # wrote.
                return {}, frozenset(), f"was given `--{option}=` with an empty value"
            parsed[option] = inline
            continue
        if not remaining:
            return {}, frozenset(), f"was given `--{option}` with no value after it"
        if remaining[0].startswith("--"):
            # `--scope --bogus` is a missing value, not a scope named
            # `--bogus`. Swallowing the next option as a value produced a
            # refusal about the wrong word entirely, and hid the option the
            # operator forgot to fill in.
            return {}, frozenset(), (
                f"was given `--{option}` with no value after it — "
                f"`{remaining[0]}` is another option"
            )
        parsed[option] = remaining.pop(0)
    return parsed, frozenset(given), None


def _missing(command: Command, named: Mapping[str, str]) -> str | None:
    """The sentence naming each required option absent, or `None`.

    Asked of a leaf and of a command without leaves alike, so `requires` means
    the same thing wherever it is declared.
    """
    absent = [option for option in command.requires if option not in named]
    if not absent:
        return None
    return "needs " + ", ".join(f"`--{option} <{option}>`" for option in absent)


def _split_options(
    words: Sequence[str], *, flags: tuple[str, ...] = ()
) -> tuple[tuple[str, ...], list[str]]:
    """Positionals in order, and every `--name value` / `--name=value` / `--flag` word.

    A `--name` takes the word after it as its value unless that word is another
    option, so `_options` still names a missing value as missing. A declared
    flag never takes the word after it (story 4t): `goal delete --yes g_a`
    reads `g_a` as the id. A bare `--` ends the options: every word after it is
    positional, so a file whose name starts with `--` can still be named.
    """
    positional: list[str] = []
    options: list[str] = []
    remaining = list(words)
    while remaining:
        word = remaining.pop(0)
        if word == "--":
            positional.extend(remaining)
            break
        if not word.startswith("--"):
            positional.append(word)
            continue
        options.append(word)
        if word[2:] in flags:
            continue
        if "=" not in word and remaining and not remaining[0].startswith("--"):
            options.append(remaining.pop(0))
    return tuple(positional), options


def _run(handler: Callable[[Context], int], context: Context) -> int:
    """Call one handler, turning its refusal into the code the table assigns.

    Only `Refusal` is caught. Everything else belongs to `pm_ai.app.entry`'s
    guard, which is the one place a traceback is printed — catching broadly here
    would turn a bug into a tidy message and hide it.
    """
    try:
        return handler(context)
    except Refusal as refused:
        _echo(f"pm-ai: {refused}", file=sys.stderr)
        return EXIT_REFUSAL
