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

from pm_ai.core.goal_register import DOMAIN_SPELLINGS, HORIZON_SPELLINGS
from pm_ai.surfaces.cli.logs import LEVELS as LOG_LEVELS
from pm_ai.surfaces.cli.dispatch import (
    GOAL_OPTIONS,
    MEETING_ARGUMENT,
    SCOPE_ARGUMENT,
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
    }
)
"""`(command, name)` pairs whose value is free text, offered nothing."""

FREE_TEXT_NAMES: frozenset[str] = frozenset({"alias", _TITLE, MEETING_ARGUMENT})
"""Names that are free text wherever a command declares them.

`--meeting` is a meeting id, which is not completed: the ids need the meeting
records, a later slice.
"""

SCOPES = "scopes"
GRAPH_INSTANCES = "graph instances"
SYSTEMS = "systems"
DOMAINS = "domains"
HORIZONS = "horizons"
GOAL_IDS = "goal ids"
LOG_LEVEL_NAMES = "log levels"
PATHS = "paths"

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
    (("connector", "add"), "system"): SYSTEMS,
    (("goal", "show"), "id"): GOAL_IDS,
    (("goal", "update"), "id"): GOAL_IDS,
    (("goal", "delete"), "id"): GOAL_IDS,
    (("logs",), "level"): LOG_LEVEL_NAMES,
}
"""Names whose value depends on the command declaring them."""


def source_of(command: tuple[str, ...], name: str) -> str | None:
    """The value source for `name` as `command` declares it; `None` for free text.

    Raises `LookupError` for a name that is neither: the test over `TABLE`
    turns that into a failure naming the command and the name.
    """
    if (command, name) in FREE_TEXT or name in FREE_TEXT_NAMES:
        return None
    if (command, name) in _BY_COMMAND:
        return _BY_COMMAND[(command, name)]
    if name in _BY_NAME:
        return _BY_NAME[name]
    raise LookupError(
        f"`{' '.join(command)}` declares `{name}`, which has no value source in "
        f"completion.py and is not listed there as free text"
    )


def _no_values() -> Sequence[str]:
    return ()


@dataclass(frozen=True, slots=True)
class Readers:
    """The local files completion reads, each as a call the composition root built.

    `projects` answers the enrolled project ids (`projects.toml`),
    `graph_instances` every Graph row's instance in `connectors/`, enabled or
    not, and `goal_ids` the ids in `strategic_goals.md`. None of them may
    touch the master key, the keychain, `operational.db` or the daemon.
    """

    projects: Callable[[], Sequence[str]] = _no_values
    graph_instances: Callable[[], Sequence[str]] = _no_values
    goal_ids: Callable[[], Sequence[str]] = _no_values


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
    for word in rest:
        if awaiting is not None:
            if word.startswith("--"):
                awaiting = None
            else:
                awaiting = None
                continue
        if ended or not word.startswith("--"):
            positionals += 1
            continue
        if word == "--":
            ended = True
            continue
        name, assigned, _ = word[2:].partition("=")
        given.add(name)
        if name in entry.flags or assigned:
            continue
        awaiting = name
    return _Reading(command, entry, positionals, frozenset(given), awaiting, ended)


@dataclass
class Completion:
    """The prompt's completer and hint over `TABLE`, with readers of local files."""

    readers: Readers = field(default_factory=Readers)
    _projects: _Cached = field(init=False)
    _graph: _Cached = field(init=False)
    _goals: _Cached = field(init=False)

    def __post_init__(self) -> None:
        self._projects = _Cached(self.readers.projects)
        self._graph = _Cached(self.readers.graph_instances)
        self._goals = _Cached(self.readers.goal_ids)

    def forget(self) -> None:
        """Read every file again at the next Tab that needs it (a command ran)."""
        for cached in (self._projects, self._graph, self._goals):
            cached.forget()

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
        entry = reading.entry
        positional = (*entry.takes, *entry.optional)
        if reading.ended:
            # After a bare `--` a word starting with `--` is a positional.
            if reading.positionals < len(positional):
                name = positional[reading.positionals]
                return offered(self._values(reading.command, name, current))
            return []
        if reading.awaiting is not None and not current.startswith("--"):
            if reading.awaiting not in entry.options:
                return []
            return offered(self._values(reading.command, reading.awaiting, current))
        if current.startswith("--"):
            name, assigned, partial = current[2:].partition("=")
            if assigned:
                if name not in entry.options:
                    return []
                return offered(
                    self._values(reading.command, name, partial), prefix=f"--{name}="
                )
            return offered(self._unused(entry, reading.given))
        if reading.positionals < len(positional):
            name = positional[reading.positionals]
            return offered(self._values(reading.command, name, current))
        if not current:
            return offered(self._unused(entry, reading.given))
        return []

    def _unused(self, entry: Command, given: frozenset[str]) -> list[str]:
        """The options and flags not yet given, spelled `--name`."""
        return [f"--{name}" for name in (*entry.options, *entry.flags) if name not in given]

    def _values(self, command: tuple[str, ...], name: str, partial: str) -> Sequence[str]:
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
        if source == PATHS:
            return _paths(partial)
        return ()

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
