"""`pm-ai logs` — the daemon's diagnostic log, read as plain lines (story 4w).

The daemon writes `~/.pm-ai/logs/daemon.log` (AD-24) as one JSON object per
line with UTC times, and rotates it into `daemon.log.1` to `.3`. This module
reads those files — never through the daemon, never writing, truncating or
rotating them — and prints each record as one readable line in this machine's
local zone:

    2026-10-18 10:04:05  info  request  GET /v1/status 401

Only four fields of a record are ever printed: `at`, `level`, the logger's name
(`request` for the request log, `daemon` for the daemon's own lines) and
`message`. A line that is not pm-ai's format is counted and skipped, never
printed raw, so no exception text and nothing a record carries beside those four
fields reaches the terminal. Every control character in what is printed is
replaced by `?`: the request log records decoded paths from requests nobody
authenticated, so anything on the machine can plant an escape sequence there.

The file list, the logger names and the shown path are handed in by the
composition root (`pm_ai.app.entry`), which builds them from paths alone; this
module may not locate files (AD-30) and reads only what it is given.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, BinaryIO, TextIO, cast

__all__ = [
    "DEFAULT_LINES",
    "DaemonLog",
    "FOLLOW_INTERVAL_SECONDS",
    "LEVELS",
    "LogUnreadable",
    "MAX_LINES",
    "Record",
    "format_record",
    "level_value",
    "lines_value",
    "parse_record",
    "readable",
    "show",
    "unbuffered",
    "zone_line",
]

LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error", "critical")
"""The levels a record may carry, least severe first; `--level` takes one."""

DEFAULT_LINES = 50
MAX_LINES = 10_000

FOLLOW_INTERVAL_SECONDS = 0.5
"""How often `--follow` looks for new lines: about twice a second."""

CONTINUATION_INDENT = "    "
"""What a multi-line message's later lines are printed beneath its first with."""

_DIGITS = re.compile(r"[0-9]+")

_sleep = time.sleep
"""The wait between looks; a module seam so a test can end a follow."""

# C0, DEL and C1 — the escape character among them — and the surrogates a JSON
# string can hold but no terminal can be written.
_UNPRINTABLE = re.compile("[\x00-\x1f\x7f-\x9f\ud800-\udfff]")


class LogUnreadable(Exception):
    """The current log file exists and could not be read; the CLI refuses (exit 3)."""


@dataclass(frozen=True, slots=True)
class DaemonLog:
    """Where the daemon's log is, as the composition root located it.

    `files` are oldest first, the current file last (`daemon.log.3` … `.1`,
    `daemon.log`) — the storage service's one rule. `sources` maps a logger's
    name to the word printed for it. `shown` is how the current file is named
    to a person (`~/.pm-ai/logs/daemon.log`). `zone_name` is read by the
    composition root, since this layer may not read the environment.
    """

    files: tuple[Path, ...]
    sources: Mapping[str, str] = field(default_factory=dict)
    shown: str = ""
    zone_name: str | None = None
    """This machine's zone by name, as the platform reads it; `None` when it has none."""

    @property
    def current(self) -> Path:
        return self.files[-1]

    @property
    def rotated(self) -> tuple[Path, ...]:
        """The rotated copies, oldest first."""
        return self.files[:-1]

    def named(self, path: Path) -> str:
        """`path` as a person reads it: beside `shown`, under the same folder."""
        if not self.shown:
            return str(path)
        folder = self.shown.rpartition("/")[0]
        return f"{folder}/{path.name}" if folder else path.name


@dataclass(frozen=True, slots=True)
class Record:
    """The four fields of a log record that may be printed, and nothing else."""

    at: datetime
    level: str
    logger: str | None
    message: str


def parse_record(raw: bytes) -> Record | None:
    """One line of the file as a record, or `None` when it is not pm-ai's format.

    Not pm-ai's format: not UTF-8, not a JSON object, an `at` that is not a
    timezone-aware ISO instant, a `level` outside `LEVELS`, or a `message`
    that is not text. Every other field is dropped here, so nothing else a
    record carries can be printed.
    """
    try:
        text = raw.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    at, level, message = value.get("at"), value.get("level"), value.get("message")
    if not isinstance(at, str) or not isinstance(message, str):
        return None
    if not isinstance(level, str) or level not in LEVELS:
        return None
    try:
        instant = datetime.fromisoformat(at)
    except ValueError:
        return None
    if instant.tzinfo is None or instant.utcoffset() is None:
        return None
    logger = value.get("logger")
    return Record(instant, level, logger if isinstance(logger, str) else None, message)


def readable(text: str) -> str:
    """`text` with every control character replaced by `?`."""
    return _UNPRINTABLE.sub("?", text)


def format_record(
    record: Record, sources: Mapping[str, str], local: tzinfo | None = None
) -> str:
    """`<local time>  <level>  <source>  <message>`, continuation lines indented.

    The time is the record's instant in this machine's zone, at the offset in
    force at that instant — `astimezone()` asks the zone rules for each
    instant, so lines either side of a daylight-saving change each read in
    their own offset. `local` stands in for the machine's zone (tests).
    """
    shown_at = record.at.astimezone(local)
    if record.logger is None:
        source = "-"
    else:
        source = sources.get(record.logger, record.logger)
    first, *rest = record.message.replace("\r\n", "\n").split("\n")
    head = (
        f"{shown_at:%Y-%m-%d %H:%M:%S}  {record.level}  {readable(source)}  {readable(first)}"
    )
    return "\n".join([head, *(f"{CONTINUATION_INDENT}{readable(line)}" for line in rest)])


def _offset(delta: timedelta | None) -> str:
    minutes = int((delta or timedelta()).total_seconds() // 60)
    sign = "-" if minutes < 0 else "+"
    hours, minutes = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def zone_line(
    log: DaemonLog,
    *,
    copies: Sequence[Path] = (),
    now: datetime | None = None,
    local: tzinfo | None = None,
) -> str:
    """The first line: which file is read, and the zone times are shown in.

    `copies` are the rotated copies read with it, in any order; only those are
    named — none, `daemon.log.1`, or a range `daemon.log.1–.3`. `local` stands
    in for the machine's zone (tests); a zone with a name (`ZoneInfo`) is named
    by it, one without is "local time".
    """
    present = (now or datetime.now(timezone.utc)).astimezone(local)
    name = log.zone_name if local is None else getattr(local, "key", None)
    shown = log.shown or str(log.current)
    rotated = ""
    ordered = [path for path in reversed(log.rotated) if path in copies]
    if len(ordered) == 1:
        rotated = f" (and {ordered[0].name})"
    elif ordered:
        rotated = f" (and {ordered[0].name}–.{ordered[-1].name.rpartition('.')[2]})"
    where = f"{name} ({_offset(present.utcoffset())} now)" if name else (
        f"local time ({_offset(present.utcoffset())} now)"
    )
    return f"pm-ai: reading {shown}{rotated}, times in {where}"


class _Lines:
    """Records read, filtered and formatted; foreign lines counted."""

    def __init__(self, log: DaemonLog, threshold: int, local: tzinfo | None) -> None:
        self.log = log
        self.threshold = threshold
        self.local = local
        self.skipped = 0

    def take(self, raw: bytes) -> str | None:
        if not raw.strip():
            return None
        record = parse_record(raw.rstrip(b"\r"))
        if record is None:
            self.skipped += 1
            return None
        if LEVELS.index(record.level) < self.threshold:
            return None
        try:
            return format_record(record, self.log.sources, self.local)
        except (OverflowError, ValueError):
            # An instant this machine's zone cannot reach (year 1 east of UTC,
            # year 9999 in a positive zone): not a time pm-ai wrote.
            self.skipped += 1
            return None


def _split(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Complete lines, and the tail with no line break yet (held, not counted)."""
    *complete, tail = buffer.split(b"\n")
    return complete, tail


def _identity(handle: BinaryIO) -> tuple[int, int]:
    status = os.fstat(handle.fileno())
    return status.st_dev, status.st_ino


def _reason(error: OSError) -> str:
    return error.strerror or type(error).__name__


@dataclass
class _Follower:
    """The current file, open, with what has not yet ended in a line break."""

    handle: BinaryIO | None = None
    held: bytes = b""
    announced: bool = False
    """Whether the first line, naming the file and the zone, has been printed."""


def show(
    log: DaemonLog,
    *,
    lines: int = DEFAULT_LINES,
    level: str = LEVELS[0],
    follow: bool = False,
    out: TextIO,
    err: TextIO,
    sleep: Callable[[float], None] | None = None,
    now: datetime | None = None,
    local: tzinfo | None = None,
) -> int:
    """Print the last `lines` records at `level` or above; with `follow`, keep going.

    Returns 0. Raises `LogUnreadable` when the current file cannot be read.
    Ctrl-C while following ends it with 0; without `follow` it propagates, to
    the console script's own interruption. Output closed early (`| head`) ends
    it quietly with 0. `sleep`, `now` and `local` (the machine's zone) are
    seams for tests.
    """
    reader = _Lines(log, LEVELS.index(level), local)
    pause = _sleep if sleep is None else sleep
    state = _Follower()
    try:
        try:
            started = _start(
                log, reader, state, lines, follow=follow, out=out, err=err, now=now, local=local
            )
            if not started and not follow:
                return 0
            if follow:
                _follow(log, reader, state, out=out, err=err, sleep=pause, local=local)
        except KeyboardInterrupt:
            if not follow:
                raise
        except LogUnreadable:
            # The count is reported before the refusal, never lost with it.
            if reader.skipped:
                _skipped(reader.skipped, err)
            raise
        if reader.skipped:
            _skipped(reader.skipped, err)
        return 0
    except BrokenPipeError:
        return 0
    finally:
        if state.handle is not None:
            state.handle.close()


class _Unbuffered(io.TextIOBase):
    """Text written straight to a raw stream, so nothing is ever left buffered.

    When the reader of a pipe goes away (`pm-ai logs --follow | head -1`), a
    buffered standard output keeps the lines it could not deliver, and the
    interpreter's flush at exit fails again with "Exception ignored … Broken
    pipe" and exit 120. Written through the raw stream, a refused write leaves
    nothing behind for that flush.
    """

    def __init__(self, raw: Any, encoding: str, errors: str) -> None:
        self._raw = raw
        self._encoding = encoding
        self._errors = errors

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        data = memoryview(text.encode(self._encoding, self._errors))
        while data:
            written = self._raw.write(data)
            if written is None:
                # A non-blocking descriptor with no room: wait and try again.
                time.sleep(0.01)
                continue
            data = data[written:]
        return len(text)

    def flush(self) -> None:
        return None


def unbuffered(out: TextIO) -> TextIO:
    """`out`, written through its raw stream when it has one (the process's stdout).

    What is already buffered is flushed first. A stream with no raw layer — a
    test's buffer, the daemon's per-request buffer — is returned as it is.
    """
    raw = getattr(getattr(out, "buffer", None), "raw", None)
    if raw is None:
        return out
    out.flush()
    return cast(
        TextIO,
        _Unbuffered(raw, out.encoding or "utf-8", out.errors or "strict"),
    )


def _skipped(count: int, err: TextIO) -> None:
    noun, verb = ("line", "was") if count == 1 else ("lines", "were")
    err.write(f"pm-ai: {count} {noun} {verb} not in pm-ai's log format and {verb} skipped.\n")
    err.flush()


def _no_file(log: DaemonLog, err: TextIO, *, follow: bool) -> None:
    shown = log.shown or str(log.current)
    waiting = " Waiting for it to appear; Ctrl-C stops." if follow else ""
    err.write(
        f"pm-ai: there is no daemon log yet. The daemon writes it at {shown} "
        f"when it runs (`pm-ai daemon run`).{waiting}\n"
    )
    err.flush()


def _open(path: Path, log: DaemonLog) -> BinaryIO | None:
    """`path` open for reading; `None` when absent. Refuses an unreadable current file."""
    try:
        return path.open("rb")
    except FileNotFoundError:
        return None
    except OSError as failed:
        raise LogUnreadable(
            f"the daemon log {log.named(path)} could not be read ({_reason(failed)})."
        ) from failed


def _start(
    log: DaemonLog,
    reader: _Lines,
    state: _Follower,
    count: int,
    *,
    follow: bool,
    out: TextIO,
    err: TextIO,
    now: datetime | None,
    local: tzinfo | None,
) -> bool:
    """Print the header and the last `count` lines; `False` when there is no log at all.

    The current file is opened first and kept, so a rotation while the copies
    are opened is seen: a copy that is the same file as one already open is
    read once.
    """
    current = _open(log.current, log)
    opened: list[tuple[Path, BinaryIO]] = []
    seen: set[tuple[int, int]] = set() if current is None else {_identity(current)}
    unreadable: list[Path] = []
    try:
        for path in reversed(log.rotated):
            try:
                handle: BinaryIO = path.open("rb")
            except FileNotFoundError:
                continue
            except OSError:
                unreadable.append(path)
                continue
            if _identity(handle) in seen:
                handle.close()
                continue
            seen.add(_identity(handle))
            opened.append((path, handle))
        if current is None and not opened:
            _no_file(log, err, follow=follow)
            for path in reversed(unreadable):
                _copy_unreadable(log, path, err)
            return False
        err.write(
            zone_line(log, copies=[path for path, _ in opened], now=now, local=local) + "\n"
        )
        err.flush()
        state.announced = True
        for path in reversed(unreadable):
            _copy_unreadable(log, path, err)
        last: deque[str] = deque(maxlen=count)
        for path, handle in reversed(opened):
            try:
                data = handle.read()
            except OSError:
                _copy_unreadable(log, path, err)
                continue
            complete, tail = _split(data)
            # A rotated copy is finished: its last line is not still being written.
            for raw in [*complete, tail]:
                shown = reader.take(raw)
                if shown is not None:
                    last.append(shown)
        if current is not None:
            try:
                data = current.read()
            except OSError as failed:
                raise LogUnreadable(
                    f"the daemon log {log.named(log.current)} could not be read "
                    f"({_reason(failed)})."
                ) from failed
            complete, state.held = _split(data)
            for raw in complete:
                shown = reader.take(raw)
                if shown is not None:
                    last.append(shown)
            state.handle = current
            current = None
        if last:
            out.write("\n".join(last) + "\n")
        out.flush()
        return True
    finally:
        for _, handle in opened:
            handle.close()
        if current is not None:
            current.close()


def _copy_unreadable(log: DaemonLog, path: Path, err: TextIO) -> None:
    err.write(f"pm-ai: {log.named(path)} could not be read and was skipped.\n")
    err.flush()


def _emit(raws: Iterable[bytes], reader: _Lines, out: TextIO) -> None:
    printed = [shown for raw in raws if (shown := reader.take(raw)) is not None]
    if printed:
        out.write("\n".join(printed) + "\n")
        out.flush()


def _drain(state: _Follower, reader: _Lines, out: TextIO) -> None:
    """Print every complete line written to the open file since the last look."""
    if state.handle is None:
        return
    data = state.handle.read()
    if not data:
        return
    complete, state.held = _split(state.held + data)
    _emit(complete, reader, out)


def _finish(state: _Follower, reader: _Lines, out: TextIO) -> None:
    """The open file was rotated away: read it to its end and close it."""
    _drain(state, reader, out)
    if state.held:
        _emit([state.held], reader, out)
    state.held = b""
    if state.handle is not None:
        state.handle.close()
    state.handle = None


def _status(path: Path) -> tuple[int, int] | None:
    try:
        status = path.stat()
    except FileNotFoundError:
        return None
    return status.st_dev, status.st_ino


def _follow(
    log: DaemonLog,
    reader: _Lines,
    state: _Follower,
    *,
    out: TextIO,
    err: TextIO,
    sleep: Callable[[float], None],
    local: tzinfo | None,
) -> None:
    """Keep printing new lines until Ctrl-C, following the daemon's rotations.

    Each look: the open file is read for what was added. When `daemon.log` is
    another file than the one open, the daemon rotated: the open file is read
    to its end through the handle already held, then every copy newer than it
    (`daemon.log.1` after two rotations between looks), then the new
    `daemon.log` from its start. A moment with no `daemon.log` is waited out.
    """
    while True:
        try:
            _drain(state, reader, out)
            current = _status(log.current)
            if current is not None and (
                state.handle is None or _identity(state.handle) != current
            ):
                ours = None if state.handle is None else _identity(state.handle)
                _finish(state, reader, out)
                # The new current file is opened before the copies are read,
                # so a rotation in between cannot take it out of reach: the
                # handle keeps it, and the walk stops at it.
                state.handle = _open(log.current, log)
                if ours is not None:
                    fresh = None if state.handle is None else _identity(state.handle)
                    _newer_copies(log, ours, fresh, reader, out, err)
                if state.handle is not None and not state.announced:
                    # It did not exist when `--follow` started: name it now.
                    err.write(zone_line(log, local=local) + "\n")
                    err.flush()
                    state.announced = True
                _drain(state, reader, out)
        except BrokenPipeError:
            raise
        except OSError as failed:
            # A read of the open file, or a look at the folder, failed.
            raise LogUnreadable(
                f"the daemon log {log.named(log.current)} could not be read "
                f"({_reason(failed)})."
            ) from failed
        sleep(FOLLOW_INTERVAL_SECONDS)


def _newer_copies(
    log: DaemonLog,
    ours: tuple[int, int],
    fresh: tuple[int, int] | None,
    reader: _Lines,
    out: TextIO,
    err: TextIO,
) -> None:
    """Read whole every rotated copy newer than `ours` and older than `fresh`.

    `ours` is the file that was open; `fresh` the current file now open, which
    is drained through its own handle. The copies are opened first and told
    apart by the identity of each open file, oldest first: the ones after
    `ours` were written after it. When `ours` is among none of them it was
    removed rather than rotated, and no copy is newer than what was shown.
    """
    handles: list[tuple[Path, BinaryIO, tuple[int, int]]] = []
    unreadable: list[Path] = []
    try:
        for path in log.rotated:
            try:
                handle: BinaryIO = path.open("rb")
            except FileNotFoundError:
                continue
            except OSError:
                unreadable.append(path)
                continue
            handles.append((path, handle, _identity(handle)))
        at = next(
            (index for index, (_, _, found) in enumerate(handles) if found == ours), None
        )
        if at is None:
            return
        for path in unreadable:
            _copy_unreadable(log, path, err)
        for path, handle, found in handles[at + 1 :]:
            if found == fresh:
                break
            try:
                data = handle.read()
            except OSError:
                _copy_unreadable(log, path, err)
                continue
            complete, tail = _split(data)
            _emit([*complete, tail], reader, out)
    finally:
        for _, handle, _ in handles:
            handle.close()


def lines_value(value: str | None) -> int | None:
    """`--lines`'s value as a count, or `None` when it is not 1 to 10000 in ASCII digits."""
    if value is None:
        return DEFAULT_LINES
    if not _DIGITS.fullmatch(value):
        return None
    if len(value) > len(str(MAX_LINES)):
        # Refused by length before `int()`, which raises on thousands of
        # digits. Leading zeros within five digits (`00050`) still read.
        return None
    count = int(value.lstrip("0") or "0")
    return count if 1 <= count <= MAX_LINES else None


def level_value(value: str | None) -> str | None:
    """`--level`'s value, lower case, or `None` when it names no level."""
    if value is None:
        return LEVELS[0]
    lowered = value.lower()
    return lowered if lowered in LEVELS else None

