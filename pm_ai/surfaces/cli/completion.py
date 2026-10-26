"""What the `pm-ai>` prompt offers on Tab, and the hint it shows (story 4u).

Every command, subcommand, option and flag candidate is read from the one
command table, `TABLE`. A value is completed by the command and the name it
declares for that argument or option, so one name can mean two things:
`instance` is an enrolled Graph row after `connector sign-in` and a new name
after `connector add`. Each declared name has a value source here, or is listed
here as free text (`FREE_TEXT`, `FREE_TEXT_NAMES`); a test fails when a command
declares a name that is neither, so a new argument cannot go uncompleted
silently.

This module is plain Python and imports no line-editing library: the prompt
(`repl`) adapts it to `prompt_toolkit`. It does no I/O of its own beyond
listing a folder to complete a path. The enrolled projects, the Graph rows and
the goal ids come from `Readers`, which the composition root builds from paths
(`surfaces` may not locate files). Each reader runs at the first Tab that needs
it, at most once between two commands, and again after a command runs
(`Completion.forget`), so a project enrolled in this session is offered next. A
reader that fails offers nothing for that value; completion never prints.

Nothing here reaches the daemon.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pm_ai.core.goal_register import DOMAIN_SPELLINGS, HORIZON_SPELLINGS
from pm_ai.core.meeting_listing import shown_title, title_words
from pm_ai.domain.meeting_listing import LIST_STATES
from pm_ai.surfaces.cli.logs import LEVELS as LOG_LEVELS
from pm_ai.core.commitment_cards import LIST_STATES as COMMITMENT_STATES
from pm_ai.core.proposal_cards import LIST_STATES as PROPOSAL_STATES
from pm_ai.core.proposals import TODAYS_TYPES
from pm_ai.surfaces.cli.dispatch import (
    COMMITMENT_ARGUMENT,
    NAME_ARGUMENT,
    NOTE_OPTION,
    WHEN_ARGUMENT,
    DUE_OPTION,
    GOAL_OPTIONS,
    IF_VERSION_OPTION,
    LIMIT_OPTION,
    MEETING_ARGUMENT,
    ON_OPTION,
    OWNER_OPTION,
    PROPOSAL_ARGUMENT,
    SCOPE_ARGUMENT,
    STATE_OPTION,
    TYPE_OPTION,
    WHAT_OPTION,
    SUMMARY_MAKERS,
    SUPPORTED_SYSTEMS,
    TABLE,
    Command,
    _spelled,
)

__all__ = [
    "Candidate",
    "Completion",
    "FREE_TEXT",
    "FREE_TEXT_NAMES",
    "ENDING_WORDS",
    "HELP_WORDS",
    "Readers",
    "Split",
    "source_of",
    "split_line",
]

PROPOSAL_TYPES = tuple(each.name for each in TODAYS_TYPES)
"""`proposal list --type`'s offers: the types pm-ai registers today (13a)."""

HELP_WORDS = ("help", "-h", "--help")
"""The words that ask for every command (`dispatch._HELP_FLAGS`)."""

ENDING_WORDS = ("exit", "quit")
"""The words that end the session (`repl.ENDINGS`), offered as first words."""

_TITLE = GOAL_OPTIONS[2]

FREE_TEXT: frozenset[tuple[tuple[str, ...], str]] = frozenset(
    {
        # A new connector's name: nothing on file can be offered for it.
        (("connector", "add"), "instance"),
        # A new goal's id, or one on file — `goal set` creates or revises.
        (("goal", "set"), "id"),
        # How many log lines: a number, nothing on file to offer (story 4w).
        (("logs",), "lines"),
        # A drop's meeting options (stories 11d, 11e): a start, handles and
        # minutes, none of them on file to offer. `--title` is free text
        # already, by `FREE_TEXT_NAMES` (it is `goal set`'s `title` too).
        (("transcript", "drop"), "start"),
        (("transcript", "drop"), "attendees"),
        (("transcript", "drop"), "duration"),
        # Story 11f: a date, a count and a title's words: nothing on file to offer.
        (("transcript", "drop"), ON_OPTION),
        (("transcript", "summarize"), ON_OPTION),
        (("transcript", "list"), LIMIT_OPTION),
        # Story 7e: a speaker label as said in a meeting, typed by the PM.
        (("member", "name"), "label"),
        # Story 13a: a count, a date, a version and the PM's own words.
        (("proposal", "list"), LIMIT_OPTION),
        (("proposal", "list"), ON_OPTION),
        (("proposal", "review"), ON_OPTION),
        (("proposal", "approve"), IF_VERSION_OPTION),
        (("proposal", "reject"), IF_VERSION_OPTION),
        (("proposal", "edit"), IF_VERSION_OPTION),
        (("proposal", "edit"), WHAT_OPTION),
        (("proposal", "edit"), OWNER_OPTION),
        (("proposal", "edit"), DUE_OPTION),
        # Story 15c: a count, a date, a version, a due's words and the PM's own words.
        (("commitment", "list"), LIMIT_OPTION),
        (("commitment", "list"), ON_OPTION),
        (("commitment", "review"), ON_OPTION),
        (("commitment", "done"), IF_VERSION_OPTION),
        (("commitment", "altered"), IF_VERSION_OPTION),
        (("commitment", "broken"), IF_VERSION_OPTION),
        (("commitment", "withdrawn"), IF_VERSION_OPTION),
        (("commitment", "due"), IF_VERSION_OPTION),
        (("commitment", "owner"), IF_VERSION_OPTION),
        (("commitment", "undo"), IF_VERSION_OPTION),
        (("commitment", "done"), NOTE_OPTION),
        (("commitment", "altered"), NOTE_OPTION),
        (("commitment", "broken"), NOTE_OPTION),
        (("commitment", "withdrawn"), NOTE_OPTION),
        (("commitment", "altered"), WHAT_OPTION),
        (("commitment", "altered"), OWNER_OPTION),
        (("commitment", "altered"), DUE_OPTION),
        (("commitment", "due"), WHEN_ARGUMENT),
    }
)
"""`(command, name)` pairs whose value is free text, offered nothing."""

FREE_TEXT_NAMES: frozenset[str] = frozenset({"alias", _TITLE, MEETING_ARGUMENT})
"""Names that are free text wherever a command declares them.

`--meeting` is free text unless `_BY_COMMAND`, consulted first, gives it a
source: `member name` (story 7e), and `transcript summarize` and `transcript
drop` (story 11f) offer the meetings of the line's scope.
"""

SCOPES = "scopes"
GRAPH_INSTANCES = "graph instances"
SYSTEMS = "systems"
DOMAINS = "domains"
HORIZONS = "horizons"
GOAL_IDS = "goal ids"
LOG_LEVEL_NAMES = "log levels"
SUMMARY_MAKER_NAMES = "summary makers"
PATHS = "paths"
MEMBERS = "project members"
MEETING_IDS = "meeting ids"
SUMMARIZABLE = "summarizable meetings"
DROPPABLE = "started meetings"
LIST_STATE_NAMES = "list states"
UPDATE_IDS = "update ids"
PROPOSAL_IDS = "proposal ids"
PROPOSAL_STATE_NAMES = "proposal states"
PROPOSAL_TYPE_NAMES = "proposal types"
COMMITMENT_IDS = "commitment ids"
COMMITMENT_STATE_NAMES = "commitment states"
COMMITMENT_OWNERS = "commitment owners"

MEETING_CANDIDATES = 100
"""At most this many meetings are offered for one Tab, after filtering (11f)."""

_BY_NAME: Mapping[str, str] = {
    SCOPE_ARGUMENT: SCOPES,
    GOAL_OPTIONS[0]: DOMAINS,
    GOAL_OPTIONS[1]: HORIZONS,
    "file": PATHS,
    "path": PATHS,
}
"""Names that mean the same value wherever they are declared."""

_BY_COMMAND: Mapping[tuple[tuple[str, ...], str], str] = {
    (("connector", "sign-in"), "instance"): GRAPH_INSTANCES,
    (("connector", "categories"), "instance"): GRAPH_INSTANCES,
    (("connector", "add"), "system"): SYSTEMS,
    (("goal", "show"), "id"): GOAL_IDS,
    (("goal", "update"), "id"): GOAL_IDS,
    (("goal", "delete"), "id"): GOAL_IDS,
    (("logs",), "level"): LOG_LEVEL_NAMES,
    # Story 7b: who makes a summary — `local`, or `claude` (story 7c).
    (("transcript", "summarize"), "by"): SUMMARY_MAKER_NAMES,
    # Story 7e: the members a label may name (usernames and handles), the
    # meetings a generic label may be limited to, the links awaiting a word.
    (("member", "name"), "member"): MEMBERS,
    (("member", "name"), "meeting"): MEETING_IDS,
    (("member", "link"), "update"): UPDATE_IDS,
    # Story 11f: a meeting chosen by its start and title, its id inserted —
    # for `summarize` only those ingested and kept, for `drop` every recorded
    # meeting that has started. And `transcript list --state`'s five words.
    (("transcript", "summarize"), MEETING_ARGUMENT): SUMMARIZABLE,
    (("transcript", "drop"), MEETING_ARGUMENT): DROPPABLE,
    (("transcript", "list"), STATE_OPTION): LIST_STATE_NAMES,
    # Story 13a: a proposal chosen by its owner, text and meeting, its id
    # inserted — only those the verb can act on; the listing's six states and
    # the registered types; a meeting of the line's scope.
    (("proposal", "show"), PROPOSAL_ARGUMENT): PROPOSAL_IDS,
    (("proposal", "approve"), PROPOSAL_ARGUMENT): PROPOSAL_IDS,
    (("proposal", "reject"), PROPOSAL_ARGUMENT): PROPOSAL_IDS,
    (("proposal", "edit"), PROPOSAL_ARGUMENT): PROPOSAL_IDS,
    (("proposal", "list"), STATE_OPTION): PROPOSAL_STATE_NAMES,
    (("proposal", "list"), TYPE_OPTION): PROPOSAL_TYPE_NAMES,
    (("proposal", "list"), MEETING_ARGUMENT): MEETING_IDS,
    (("proposal", "review"), MEETING_ARGUMENT): MEETING_IDS,
    # Story 15c: a commitment chosen by its owner, text, meeting and due, its id
    # inserted — only those the verb can act on; the listing's five states; an
    # owner from the names already in the ledger; a meeting of the line's scope.
    **{
        (("commitment", verb), COMMITMENT_ARGUMENT): COMMITMENT_IDS
        for verb in ("show", "done", "altered", "broken", "withdrawn", "due", "owner", "undo")
    },
    (("commitment", "owner"), NAME_ARGUMENT): COMMITMENT_OWNERS,
    (("commitment", "list"), STATE_OPTION): COMMITMENT_STATE_NAMES,
    (("commitment", "list"), MEETING_ARGUMENT): MEETING_IDS,
    (("commitment", "review"), MEETING_ARGUMENT): MEETING_IDS,
}
"""Names whose value depends on the command declaring them."""


def source_of(command: tuple[str, ...], name: str) -> str | None:
    """The value source for `name` as `command` declares it; `None` for free text.

    Raises `LookupError` for a name that is neither: the test over `TABLE`
    turns that into a failure naming the command and the name.
    """
    # A command's own source first: `member name --meeting` offers the
    # project's meeting ids, while `--meeting` stays free text elsewhere.
    if (command, name) in _BY_COMMAND:
        return _BY_COMMAND[(command, name)]
    if (command, name) in FREE_TEXT or name in FREE_TEXT_NAMES:
        return None
    if name in _BY_NAME:
        return _BY_NAME[name]
    raise LookupError(
        f"`{' '.join(command)}` declares `{name}`, which has no value source in "
        f"completion.py and is not listed there as free text"
    )


def _no_values() -> Sequence[str]:
    return ()


def _no_project_values(project: str) -> Sequence[str]:
    return ()


ProposalChoice = tuple[str, str, str, datetime | None]
"""A proposal completion may offer (story 13a): its id, `owner: what`, its
meeting's title, and the meeting's start (in the display zone), or `None`."""

PROPOSAL_CANDIDATES = 100
"""At most this many proposals are offered for one Tab (13a)."""


def _no_proposals(verb: str) -> Sequence[ProposalChoice]:
    return ()


CommitmentChoice = tuple[str, str, str, str]
"""A commitment completion may offer (story 15c): its id, `owner: what` (or `said
by …: what`), its meeting's title, and its due as the card says it."""

COMMITMENT_CANDIDATES = 100
"""At most this many commitments are offered for one Tab (15c)."""


def _no_commitments(verb: str) -> Sequence[CommitmentChoice]:
    return ()


def _no_owners(commitment_id: str | None, scope: str | None) -> Sequence[str]:
    return ()


MeetingChoice = tuple[str, datetime, str] | tuple[str, datetime, str, bool]
"""A meeting completion may offer: its id, its start (in the display zone), its
title, and optionally whether its record holds a summary (`summarize` says so)."""


def _no_meetings(scope: str) -> Sequence[MeetingChoice]:
    return ()


@dataclass(frozen=True, slots=True)
class Readers:
    """The local files completion reads, each as a call the composition root built.

    `projects` answers the enrolled project ids (`projects.toml`),
    `graph_instances` every Graph row's instance in `connectors/`, enabled or
    not, and `goal_ids` the ids in `strategic_goals.md`. Story 7e adds, over
    every enrolled project, `members` (the GitLab members' usernames and
    handles), `meetings` (the recorded meetings' ids) and `updates` (the
    model-matched links awaiting the PM's word). None of them may touch the
    master key, the keychain or the daemon; `members` alone reads
    `operational.db`, on a read-only connection of its own, never a
    `StorageService`.
    """

    projects: Callable[[], Sequence[str]] = _no_values
    graph_instances: Callable[[], Sequence[str]] = _no_values
    goal_ids: Callable[[], Sequence[str]] = _no_values
    members: Callable[[str], Sequence[str]] = _no_project_values
    meetings: Callable[[str], Sequence[MeetingChoice]] = _no_meetings
    """Since story 11f, by scope (`personal` or `project:<id>`): every recorded
    meeting that has started, most recent first, as (id, start, title)."""
    droppable: Callable[[str], Sequence[MeetingChoice]] = _no_meetings
    """Story 11f: `meetings`, only those that have started — `drop --meeting`'s."""
    summarizable: Callable[[str], Sequence[MeetingChoice]] = _no_meetings
    """Story 11f: the scope's meetings whose transcript is ingested and kept —
    the listing's summarize check — newest first. `operational.db` is read
    read-only, the exception 4u's readers grant to `7e`."""
    updates: Callable[[str], Sequence[str]] = _no_project_values
    proposals: Callable[[str], Sequence[ProposalChoice]] = _no_proposals
    """Story 13a, by verb (`show`, `approve`, `reject`, `edit`): the proposals
    that verb can act on, newest meeting first, at most 100. Never one whose
    scope cannot be told or whose body cannot be read. `operational.db` is read
    read-only, by path, as `summarizable` reads it."""
    commitments: Callable[[str], Sequence[CommitmentChoice]] = _no_commitments
    """Story 15c, by verb: the commitments that verb can act on, in `list`'s
    order, at most 100. Each ledger is read by path with `parse_ledger` and
    `fold`; a ledger that cannot be read offers nothing."""
    commitment_owners: Callable[[str | None, str | None], Sequence[str]] = _no_owners
    """Story 15c, by commitment id and `--scope` (or `None`): the owners and
    speakers already in that scope's ledger, the commitment's own speaker first."""


# ── Splitting a line that may still be open ─────────────────────────────────


@dataclass(frozen=True, slots=True)
class Split:
    """A line read as `shlex.split` would, tolerating a quote not yet closed.

    `words` are the finished words. `current` is the word the cursor is in, as
    its value so far, starting at `start` in the text; `quote` is the quote
    still open in it, if any. A line ending in a space has an empty `current`
    starting at its end.
    """

    words: tuple[str, ...]
    current: str
    start: int
    quote: str | None
    broken: bool = False
    """A line `shlex.split` refuses for a reason other than an open quote: a
    backslash with nothing after it. Completion offers nothing for it and no
    hint is shown, as the line would not run."""


_DOUBLE_QUOTE_ESCAPES = frozenset('\\"')
"""What a backslash escapes inside double quotes for `shlex` in POSIX mode: the
quote and the backslash only. Unlike a POSIX shell, `shlex` keeps the backslash
before `$`, a backtick or anything else."""


def split_line(text: str) -> Split:
    """`text` split into words the way the prompt's `shlex.split` does.

    POSIX rules, no comments: whitespace separates words; single quotes are
    literal to the next one; inside double quotes a backslash escapes only
    `\\` and `"`; outside quotes it escapes any
    character. Where `shlex.split` would raise on an open quote, this returns
    the open word as `current` with its `quote`.
    """
    words: list[str] = []
    value: list[str] = []
    in_word = False
    start = len(text)
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                value.append(char)
        elif quote == '"':
            if char == '"':
                quote = None
            elif char == "\\" and index + 1 < len(text) and text[index + 1] in _DOUBLE_QUOTE_ESCAPES:
                index += 1
                value.append(text[index])
            else:
                value.append(char)
        elif char.isspace():
            if in_word:
                words.append("".join(value))
                value, in_word = [], False
        else:
            if not in_word:
                in_word, start = True, index
            if char in "'\"":
                quote = char
            elif char == "\\":
                if index + 1 >= len(text):
                    return Split(tuple(words), "".join(value), start, None, broken=True)
                index += 1
                value.append(text[index])
            else:
                value.append(char)
        index += 1
    if not in_word:
        return Split(tuple(words), "", len(text), None)
    return Split(tuple(words), "".join(value), start, quote)


# ── Candidates ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Candidate:
    """One completion: replace the text from `start` to the cursor with `text`.

    `display` is the value as a person reads it, unquoted.
    """

    text: str
    start: int
    display: str
    meta: str = ""
    """Shown beside the candidate, never inserted: a meeting's start and title (11f)."""


def _quoted(value: str, quote: str | None) -> str:
    """`value` written so that splitting the line gives it back unchanged.

    Inside a quote still open, the word is completed within it and the quote
    closed. Otherwise `shlex.quote`, the standard library's own inverse of the
    split the prompt does: a space, a quote, `$` or `~` puts the word in single
    quotes. A path the person began with `~/` keeps its `~/` unquoted, so the
    command still expands it.
    """
    if quote == "'" and "'" not in value:
        return f"'{value}'"
    if quote == '"':
        escaped = "".join(f"\\{char}" if char in _DOUBLE_QUOTE_ESCAPES else char for char in value)
        return f'"{escaped}"'
    if quote is None and value.startswith("~/"):
        rest = value[2:]
        return "~/" + (shlex.quote(rest) if rest else "")
    return shlex.quote(value)


@dataclass(slots=True)
class _Cached:
    """One reader, called at most once until `forget`; a failure is no values."""

    read: Callable[[], Sequence[str]]
    values: tuple[str, ...] | None = None

    def get(self) -> tuple[str, ...]:
        if self.values is None:
            try:
                self.values = tuple(value for value in self.read() if isinstance(value, str))
            except Exception:  # noqa: BLE001 — a reader that fails offers nothing
                self.values = ()
        return self.values

    def forget(self) -> None:
        self.values = None


@dataclass(slots=True)
class _CachedMeetings:
    """`_Cached` for meetings: (id, start, title) triples rather than strings (11f)."""

    read: Callable[[], Sequence[MeetingChoice]]
    values: tuple[tuple[str, datetime, str, bool], ...] | None = None

    def get(self) -> tuple[tuple[str, datetime, str, bool], ...]:
        if self.values is None:
            try:
                self.values = tuple(
                    (value[0], value[1], value[2], len(value) == 4 and value[3] is True)
                    for value in self.read()
                    if isinstance(value, tuple)
                    and len(value) in (3, 4)
                    and isinstance(value[0], str)
                    and isinstance(value[1], datetime)
                    and isinstance(value[2], str)
                )
            except Exception:  # noqa: BLE001 — a reader that fails offers nothing
                self.values = ()
        return self.values

    def forget(self) -> None:
        self.values = None


@dataclass(frozen=True, slots=True)
class _Reading:
    """Where the words before the cursor leave a command line."""

    command: tuple[str, ...]
    entry: Command
    positionals: int
    given: frozenset[str]
    awaiting: str | None
    ended: bool = False
    """A bare `--` came before: every later word is positional (`dispatch`)."""
    positional_words: tuple[str, ...] = ()
    """The finished positional words, in order (15c: `commitment owner <id> <name>`)."""


def _reading(words: Sequence[str]) -> _Reading | None:
    """The command named by `words` and what its later words have used.

    `None` when the words name no runnable command yet (an unknown word, a
    group without its subcommand). The option walk is `dispatch._split_options`'s:
    a `--name` without `=` takes the next word as its value unless that word
    is another option; a declared flag takes none; `--` ends the options.
    """
    if not words:
        return None
    entry = TABLE.get(words[0])
    if entry is None:
        return None
    command: tuple[str, ...]
    if entry.run is None:
        if len(words) < 2:
            return None
        leaf = entry.leaves.get(words[1])
        if leaf is None or leaf.run is None:
            return None
        command, rest = (words[0], words[1]), words[2:]
        entry = leaf
    else:
        command, rest = (words[0],), words[1:]
    positionals = 0
    given: set[str] = set()
    awaiting: str | None = None
    ended = False
    kept: list[str] = []
    for word in rest:
        if awaiting is not None:
            if word.startswith("--"):
                awaiting = None
            else:
                awaiting = None
                continue
        if ended or not word.startswith("--"):
            positionals += 1
            kept.append(word)
            continue
        if word == "--":
            ended = True
            continue
        name, assigned, _ = word[2:].partition("=")
        given.add(name)
        if name in entry.flags or assigned:
            continue
        awaiting = name
    return _Reading(command, entry, positionals, frozenset(given), awaiting, ended, tuple(kept))


@dataclass
class Completion:
    """The prompt's completer and hint over `TABLE`, with readers of local files."""

    readers: Readers = field(default_factory=Readers)
    _projects: _Cached = field(init=False)
    _graph: _Cached = field(init=False)
    _goals: _Cached = field(init=False)
    _scoped: dict[tuple[str, str], _Cached] = field(init=False)
    _meetings: dict[tuple[str, str], _CachedMeetings] = field(init=False)
    _proposals: dict[str, _CachedProposals] = field(init=False)
    _commitments: dict[str, _CachedCommitments] = field(init=False)
    _owners: dict[tuple[str | None, str | None], _Cached] = field(init=False)

    def __post_init__(self) -> None:
        self._projects = _Cached(self.readers.projects)
        self._graph = _Cached(self.readers.graph_instances)
        self._goals = _Cached(self.readers.goal_ids)
        self._scoped = {}
        self._meetings = {}
        self._proposals = {}
        self._commitments = {}
        self._owners = {}

    def forget(self) -> None:
        """Read every file again at the next Tab that needs it (a command ran)."""
        for cached in (
            self._projects,
            self._graph,
            self._goals,
            *self._scoped.values(),
        ):
            cached.forget()
        for meetings in self._meetings.values():
            meetings.forget()
        for proposals in self._proposals.values():
            proposals.forget()
        for commitments in self._commitments.values():
            commitments.forget()
        for owners in self._owners.values():
            owners.forget()

    # ── Tab ──────────────────────────────────────────────────────────────

    def complete(self, before_cursor: str) -> list[Candidate]:
        """Every candidate for the word the cursor ends, in the order offered."""
        split = split_line(before_cursor)
        if split.broken:
            return []
        words, current = split.words, split.current

        def offered(values: Sequence[str], *, prefix: str = "") -> list[Candidate]:
            return [
                Candidate(_quoted(prefix + value, split.quote), split.start, prefix + value)
                for value in values
                if (prefix + value).startswith(current)
            ]

        if not words:
            return offered([*TABLE, HELP_WORDS[0], *ENDING_WORDS])
        first = TABLE.get(words[0])
        if first is None:
            return []
        if first.run is None and len(words) == 1:
            return offered(list(first.leaves))
        reading = _reading(words)
        if reading is None:
            return []
        scope = _named_scope(words)
        project = scope.removeprefix("project:") if scope and scope.startswith("project:") else None
        entry = reading.entry

        def commitments_for(name: str, typed: str) -> list[Candidate] | None:
            source = source_of_or_none(reading.command, name)
            if source == COMMITMENT_IDS:
                verb = reading.command[1]
                if verb not in self._commitments:
                    read = self.readers.commitments
                    self._commitments[verb] = _CachedCommitments(lambda: read(verb))
                return _commitment_candidates(
                    self._commitments[verb].get(), typed, split.start, split.quote
                )
            if source == COMMITMENT_OWNERS:
                named = reading.positional_words[0] if reading.positional_words else None
                key = (named, scope)
                if key not in self._owners:
                    owners = self.readers.commitment_owners
                    self._owners[key] = _Cached(lambda: owners(named, scope))
                return offered(self._owners[key].get())
            return None

        def proposals_for(name: str, typed: str) -> list[Candidate] | None:
            chosen = commitments_for(name, typed)
            if chosen is not None:
                return chosen
            if source_of_or_none(reading.command, name) != PROPOSAL_IDS:
                return None
            verb = reading.command[1]
            if verb not in self._proposals:
                read = self.readers.proposals
                self._proposals[verb] = _CachedProposals(lambda: read(verb))
            return _proposal_candidates(
                self._proposals[verb].get(), typed, split.start, split.quote
            )

        def meetings_for(name: str, typed: str, prefix: str = "") -> list[Candidate] | None:
            chosen = self._meeting_choices(reading.command, name, scope)
            if chosen is None:
                return None
            return _meeting_candidates(chosen, typed, split.start, split.quote, prefix)
        positional = (*entry.takes, *entry.optional)
        if reading.ended:
            # After a bare `--` a word starting with `--` is a positional.
            if reading.positionals < len(positional):
                name = positional[reading.positionals]
                return offered(self._values(reading.command, name, current, project))
            return []
        if reading.awaiting is not None and not current.startswith("--"):
            if reading.awaiting not in entry.options:
                return []
            met = meetings_for(reading.awaiting, current)
            if met is not None:
                return met
            return offered(self._values(reading.command, reading.awaiting, current, project))
        if current.startswith("--"):
            name, assigned, partial = current[2:].partition("=")
            if assigned:
                if name not in entry.options:
                    return []
                met = meetings_for(name, partial, prefix=f"--{name}=")
                if met is not None:
                    return met
                return offered(
                    self._values(reading.command, name, partial, project), prefix=f"--{name}="
                )
            return offered(self._unused(entry, reading.given))
        if reading.positionals < len(positional):
            name = positional[reading.positionals]
            chosen = proposals_for(name, current)
            if chosen is not None:
                return chosen
            return offered(self._values(reading.command, name, current, project))
        if not current:
            return offered(self._unused(entry, reading.given))
        return []

    def _unused(self, entry: Command, given: frozenset[str]) -> list[str]:
        """The options and flags not yet given, spelled `--name`."""
        return [f"--{name}" for name in (*entry.options, *entry.flags) if name not in given]

    def _values(
        self, command: tuple[str, ...], name: str, partial: str, project: str | None = None
    ) -> Sequence[str]:
        try:
            source = source_of(command, name)
        except LookupError:
            return ()
        if source == SCOPES:
            return ["personal", *(f"project:{project}" for project in self._projects.get())]
        if source == GRAPH_INSTANCES:
            return self._graph.get()
        if source == SYSTEMS:
            return SUPPORTED_SYSTEMS
        if source == DOMAINS:
            return tuple(DOMAIN_SPELLINGS)
        if source == HORIZONS:
            return tuple(HORIZON_SPELLINGS)
        if source == GOAL_IDS:
            return self._goals.get()
        if source == LOG_LEVEL_NAMES:
            return LOG_LEVELS
        if source == SUMMARY_MAKER_NAMES:
            return SUMMARY_MAKERS
        if source == LIST_STATE_NAMES:
            return LIST_STATES
        if source == COMMITMENT_STATE_NAMES:
            return COMMITMENT_STATES
        if source == PROPOSAL_STATE_NAMES:
            return PROPOSAL_STATES
        if source == PROPOSAL_TYPE_NAMES:
            return PROPOSAL_TYPES
        if source == PATHS:
            return _paths(partial)
        readers = {
            MEMBERS: self.readers.members,
            UPDATE_IDS: self.readers.updates,
        }
        if source in readers:
            if project is None:
                return ()
            key = (source, project)
            if key not in self._scoped:
                read = readers[source]
                self._scoped[key] = _Cached(lambda: read(project))
            return self._scoped[key].get()
        return ()

    def _meeting_choices(
        self, command: tuple[str, ...], name: str, scope: str | None
    ) -> tuple[tuple[str, datetime, str, bool], ...] | None:
        """The meetings a meeting-valued name offers in `scope`; `None` when it is not one.

        Keyed by the `--scope` already on the line: `personal` or a project.
        `people:`, or no `--scope`, offers nothing (story 11f).
        """
        try:
            source = source_of(command, name)
        except LookupError:
            return None
        readers = {
            MEETING_IDS: self.readers.meetings,
            DROPPABLE: self.readers.droppable,
            SUMMARIZABLE: self.readers.summarizable,
        }
        if source not in readers:
            return None
        if scope is None or not (scope == "personal" or scope.startswith("project:")):
            return ()
        key = (source, scope)
        if key not in self._meetings:
            read = readers[source]
            self._meetings[key] = _CachedMeetings(lambda: read(scope))
        return self._meetings[key].get()

    # ── The bottom line ──────────────────────────────────────────────────

    def hint(self, text: str) -> str | None:
        """What the bottom line shows for `text`, or `None` for nothing.

        A command's usage and summary while the line names one; a group's
        subcommands while it names a group alone; a help word's meaning; and
        nothing for an empty line, an open line or an unknown word.
        """
        split = split_line(text)
        if split.broken:
            return None
        words = [*split.words, *([split.current] if split.current or split.quote else [])]
        if not words:
            return None
        first = words[0]
        if first in HELP_WORDS and len(words) == 1:
            return f"{first}: lists every command"
        entry = TABLE.get(first)
        if entry is None:
            return None
        if entry.run is not None:
            return f"{_spelled(first, entry)}  —  {entry.summary}"
        leaf = entry.leaves.get(words[1]) if len(words) > 1 else None
        if leaf is not None and leaf.run is not None:
            return f"{first} {_spelled(words[1], leaf)}  —  {leaf.summary}"
        if not entry.leaves:
            return f"{first}  —  {entry.summary}; no subcommand is implemented yet"
        return f"{first} {{{', '.join(entry.leaves)}}}  —  {entry.summary}"


def source_of_or_none(command: tuple[str, ...], name: str) -> str | None:
    try:
        return source_of(command, name)
    except LookupError:
        return None


@dataclass(slots=True)
class _CachedProposals:
    """`_Cached` for proposals: (id, owner and what, title, start) (13a)."""

    read: Callable[[], Sequence[ProposalChoice]]
    values: tuple[ProposalChoice, ...] | None = None

    def get(self) -> tuple[ProposalChoice, ...]:
        if self.values is None:
            try:
                self.values = tuple(
                    value
                    for value in self.read()
                    if isinstance(value, tuple)
                    and len(value) == 4
                    and isinstance(value[0], str)
                    and isinstance(value[1], str)
                    and isinstance(value[2], str)
                    and (value[3] is None or isinstance(value[3], datetime))
                )
            except Exception:  # noqa: BLE001 — a reader that fails offers nothing
                self.values = ()
        return self.values

    def forget(self) -> None:
        self.values = None


def _proposal_candidates(
    choices: Sequence[ProposalChoice], typed: str, start: int, quote: str | None
) -> list[Candidate]:
    """The proposals whose id starts with `typed`, or a word of whose text or title does.

    Folded as `--title` folds; at most `PROPOSAL_CANDIDATES`, each inserting its
    id and showing `owner: what` and its meeting's title and start (13a).
    """
    folded = title_words(typed)
    found: list[Candidate] = []
    for proposal_id, said, title, when in choices:
        if typed and not proposal_id.startswith(typed):
            words = (*title_words(said), *title_words(title))
            if not folded or not all(
                any(word.startswith(part) for word in words) for part in folded
            ):
                continue
        meeting = shown_title(title) if title else ""
        if when is not None:
            meeting = f"{when.strftime('%Y-%m-%d %H:%M')} {meeting}".rstrip()
        found.append(
            Candidate(
                _quoted(proposal_id, quote),
                start,
                proposal_id,
                meta=f"{said}  {meeting}".rstrip(),
            )
        )
        if len(found) >= PROPOSAL_CANDIDATES:
            break
    return found


@dataclass(slots=True)
class _CachedCommitments:
    """`_Cached` for commitments: (id, owner and what, title, due) (15c)."""

    read: Callable[[], Sequence[CommitmentChoice]]
    values: tuple[CommitmentChoice, ...] | None = None

    def get(self) -> tuple[CommitmentChoice, ...]:
        if self.values is None:
            try:
                self.values = tuple(
                    value
                    for value in self.read()
                    if isinstance(value, tuple)
                    and len(value) == 4
                    and all(isinstance(part, str) for part in value)
                )
            except Exception:  # noqa: BLE001 — a reader that fails offers nothing
                self.values = ()
        return self.values

    def forget(self) -> None:
        self.values = None


def _commitment_candidates(
    choices: Sequence[CommitmentChoice], typed: str, start: int, quote: str | None
) -> list[Candidate]:
    """The commitments whose id starts with `typed`, or a word of whose text or title does.

    Folded as `--title` folds, as 13a's proposals are matched; at most
    `COMMITMENT_CANDIDATES`, each inserting its id and showing `owner: what`,
    its meeting's title and its due (15c).
    """
    folded = title_words(typed)
    found: list[Candidate] = []
    for commitment_id, said, title, due in choices:
        if typed and not commitment_id.startswith(typed):
            words = (*title_words(said), *title_words(title))
            if not folded or not all(
                any(word.startswith(part) for word in words) for part in folded
            ):
                continue
        meta = "  ".join(part for part in (said, shown_title(title) if title else "", due) if part)
        found.append(Candidate(_quoted(commitment_id, quote), start, commitment_id, meta=meta))
        if len(found) >= COMMITMENT_CANDIDATES:
            break
    return found


def _named_scope(words: Sequence[str]) -> str | None:
    """The scope a finished `--scope` word on the line names, if any (story 11f).

    `personal`, or `project:<id>` with an id; anything else is `None`.
    """
    named: str | None = None
    for index, word in enumerate(words):
        if word == f"--{SCOPE_ARGUMENT}" and index + 1 < len(words):
            named = words[index + 1]
        elif word.startswith(f"--{SCOPE_ARGUMENT}="):
            named = word.split("=", 1)[1]
    if named == "personal":
        return named
    if named is None or not named.startswith("project:") or named == "project:":
        return None
    return named


def _meeting_candidates(
    choices: Sequence[tuple[str, datetime, str, bool]],
    typed: str,
    start: int,
    quote: str | None,
    prefix: str = "",
) -> list[Candidate]:
    """The meetings whose id starts with `typed`, or one of whose title's words does.

    Folded as `--title` folds. Filtered first, then capped at
    `MEETING_CANDIDATES`; each inserts its id and shows its start and title.
    """
    folded = title_words(typed)
    found: list[Candidate] = []
    for meeting_id, when, title, held in choices:
        if typed and not meeting_id.startswith(typed):
            words = title_words(title)
            if not folded or not all(
                any(word.startswith(part) for word in words) for part in folded
            ):
                continue
        zone = when.tzname() or ""
        found.append(
            Candidate(
                _quoted(prefix + meeting_id, quote),
                start,
                meeting_id,
                meta=f"{when.strftime('%Y-%m-%d %H:%M')} {zone}  {shown_title(title)}"
                + ("  (summary held: needs --again)" if held else ""),
            )
        )
        if len(found) >= MEETING_CANDIDATES:
            break
    return found


def _paths(partial: str) -> list[str]:
    """The entries of the folder `partial` names that start with its last part.

    Relative to the folder pm-ai was started in, `~/` expanded; the candidate
    keeps the folder as it was typed. A folder ends in `/`. Hidden entries only
    when the last part starts with `.`, and `.` or `..` alone offer `./` and
    `../`. A lone `~` offers `~/`; any other `~` form (`~name`, another
    person's home) offers nothing. A folder that cannot be listed offers
    nothing.
    """
    if partial == "~":
        return ["~/"]
    if partial.startswith("~") and not partial.startswith("~/"):
        # `~name` or `~name/...`: another person's home is never listed.
        return []
    folder, separator, prefix = partial.rpartition("/")
    typed = folder + separator
    listed = os.path.expanduser(typed) if typed else "."
    # `.` and `..` name folders that a listing does not return.
    special = [f"{name}/" for name in (".", "..") if not typed and name.startswith(prefix) and prefix]
    try:
        with os.scandir(listed) as entries:
            found = []
            for entry in entries:
                if not entry.name.startswith(prefix):
                    continue
                if entry.name.startswith(".") and not prefix.startswith("."):
                    continue
                try:
                    folder_like = entry.is_dir()
                except OSError:
                    folder_like = False
                found.append(typed + entry.name + ("/" if folder_like else ""))
    except OSError:
        return special
    return [*special, *sorted(found)]
