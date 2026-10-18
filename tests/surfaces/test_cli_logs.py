"""Story 4w — `pm-ai logs`, the reader: one test per matrix row it owns.

Spec: `_bmad-output/specs/spec-pm-ai/stories/4w-pm-ai-logs.md`.

Against log files written into a temporary folder in the daemon's own format
(`_JsonLines`), rotation simulated by renaming as the rotating handler does.
`--follow` runs with a scripted `sleep`: each look's "between checks" is the
script's next step, and the last step is Ctrl-C.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pm_ai.surfaces.cli import logs
from pm_ai.surfaces.cli.dispatch import (
    EXIT_OK,
    EXIT_REFUSAL,
    EXIT_USAGE,
    TABLE,
    dispatch,
    parse,
)
from pm_ai.domain.health import Report

REQUEST = "pm_ai.api.requests"
DAEMON = "pm_ai.daemon"
SOURCES = {REQUEST: "request", DAEMON: "daemon"}
START = datetime(2026, 10, 18, 7, 0, 0, tzinfo=timezone.utc)


UTC = ZoneInfo("UTC")


def record(n: int, *, level: str = "info", logger: str = REQUEST, message: str | None = None,
           at: datetime | None = None, **extra: object) -> bytes:
    """One line as the daemon's `_JsonLines` writes it."""
    line: dict[str, object] = {
        "at": (at or START + timedelta(seconds=n)).isoformat(),
        "logger": logger,
        "level": level,
        "message": f"GET /v1/status {n}" if message is None else message,
        **extra,
    }
    return (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")


class Folder:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.log = logs.DaemonLog(
            files=tuple(root / name for name in ("daemon.log.3", "daemon.log.2", "daemon.log.1"))
            + (root / "daemon.log",),
            sources=SOURCES,
            shown="~/.pm-ai/logs/daemon.log",
        )

    def path(self, suffix: str = "") -> Path:
        return self.root / f"daemon.log{suffix}"

    def write(self, payload: bytes, suffix: str = "") -> None:
        self.path(suffix).write_bytes(payload)

    def append(self, payload: bytes, suffix: str = "") -> None:
        with self.path(suffix).open("ab") as handle:
            handle.write(payload)

    def rotate(self) -> None:
        """What `RotatingFileHandler.doRollover` does: shift by rename, oldest dropped."""
        if self.path(".3").exists():
            self.path(".3").unlink()
        for n in (2, 1):
            if self.path(f".{n}").exists():
                self.path(f".{n}").rename(self.path(f".{n + 1}"))
        if self.path().exists():
            self.path().rename(self.path(".1"))


@pytest.fixture
def folder(tmp_path: Path) -> Folder:
    return Folder(tmp_path / "logs")


class Run:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()


def run(folder: Folder, *, follow: bool = False, steps: Sequence[Callable[[], None]] = (),
        out: io.StringIO | None = None, local: ZoneInfo = UTC, **options: object) -> Run:
    """`show` over the folder; with `follow`, each look runs the next step, then Ctrl-C."""
    remaining = list(steps)

    def sleep(_: float) -> None:
        if not remaining:
            raise KeyboardInterrupt
        remaining.pop(0)()

    written = out or io.StringIO()
    errors = io.StringIO()
    code = logs.show(folder.log, follow=follow, out=written, err=errors, sleep=sleep,
                     local=local, **options)  # type: ignore[arg-type]
    return Run(code, written.getvalue(), errors.getvalue())


def numbers(lines: list[str]) -> list[int]:
    return [int(line.rsplit(" ", 1)[1]) for line in lines]


# ── Recent lines, count, level ───────────────────────────────────────────────


def test_recent_lines_are_the_last_fifty_in_order_across_the_current_file_and_dot_1(folder):
    folder.write(b"".join(record(n) for n in range(150)), ".1")
    folder.write(b"".join(record(n) for n in range(150, 300)))
    seen = run(folder)
    assert seen.code == EXIT_OK
    assert numbers(seen.lines) == list(range(250, 300))
    first = seen.err.splitlines()[0]
    assert first == (
        "pm-ai: reading ~/.pm-ai/logs/daemon.log (and daemon.log.1), "
        "times in UTC (UTC+00:00 now)"
    )
    assert seen.lines[0] == "2026-10-18 07:04:10  info  request  GET /v1/status 250"


def test_the_first_line_names_the_zone_and_its_offset_now(folder):
    folder.write(record(1))
    seen = run(folder, local=ZoneInfo("Europe/Minsk"))
    assert seen.err.splitlines()[0].endswith("times in Europe/Minsk (UTC+03:00 now)")
    unnamed = timezone(timedelta(hours=-3, minutes=-30))
    assert logs.zone_line(folder.log, local=unnamed).endswith(
        "times in local time (UTC-03:30 now)"
    )
    berlin = ZoneInfo("Europe/Berlin")
    winter = datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert logs.zone_line(folder.log, local=berlin, now=winter).endswith(
        "times in Europe/Berlin (UTC+01:00 now)"
    )


def test_lines_5_is_the_last_five(folder):
    folder.write(b"".join(record(n) for n in range(20)))
    assert numbers(run(folder, lines=5).lines) == [15, 16, 17, 18, 19]


@pytest.mark.parametrize(
    "value", ["0", "x", "５", "10001", "-1", "1.5", " 5", "٣", "0" * 5000 + "1", "9" * 5000]
)
def test_lines_other_than_1_to_10000_in_ascii_digits_is_a_usage_error(value, capsys):
    code = dispatch(["logs", "--lines", value], daemon=None, diagnose=lambda: Report(()),
                    probe_connectors=None)
    assert code == EXIT_USAGE
    assert "--lines" in capsys.readouterr().err


def test_lines_10000_is_accepted():
    assert logs.lines_value("10000") == 10000
    assert logs.lines_value("1") == 1
    assert logs.lines_value("00050") == 50
    assert logs.lines_value("0" * 5000 + "7") is None


def test_level_warning_any_case_keeps_warning_error_and_critical(folder):
    folder.write(b"".join(
        record(n, level=level) for n, level in enumerate(logs.LEVELS)
    ))
    seen = run(folder, level=logs.level_value("Warning"))
    assert [line.split("  ")[1] for line in seen.lines] == ["warning", "error", "critical"]


def test_level_loud_is_a_usage_error_naming_the_five(capsys):
    code = dispatch(["logs", "--level", "loud"], daemon=None, diagnose=lambda: Report(()),
                    probe_connectors=None)
    assert code == EXIT_USAGE
    assert "debug, info, warning, error, critical" in capsys.readouterr().err


def test_the_count_applies_after_the_level_filter(folder):
    folder.write(b"".join(
        record(n, level="error" if n % 10 == 0 else "info") for n in range(100)
    ))
    assert numbers(run(folder, lines=3, level="error").lines) == [70, 80, 90]


# ── Rotated copies ───────────────────────────────────────────────────────────


def test_rotated_copies_are_read_oldest_first_with_no_gap(folder):
    folder.write(b"".join(record(n) for n in range(0, 10)), ".3")
    folder.write(b"".join(record(n) for n in range(10, 20)), ".2")
    folder.write(b"".join(record(n) for n in range(20, 30)), ".1")
    folder.write(b"".join(record(n) for n in range(30, 40)))
    assert numbers(run(folder, lines=40).lines) == list(range(40))


def test_an_unreadable_rotated_copy_is_skipped_with_one_sentence(folder):
    folder.write(b"".join(record(n) for n in range(0, 10)), ".3")
    folder.write(b"".join(record(n) for n in range(10, 20)), ".2")
    folder.write(b"".join(record(n) for n in range(20, 30)))
    folder.path(".2").chmod(0)
    try:
        seen = run(folder, lines=100)
    finally:
        folder.path(".2").chmod(0o600)
    assert seen.code == EXIT_OK
    assert numbers(seen.lines) == [*range(0, 10), *range(20, 30)]
    noted = [line for line in seen.err.splitlines() if "daemon.log.2" in line]
    assert noted == ["pm-ai: ~/.pm-ai/logs/daemon.log.2 could not be read and was skipped."]


# ── Time ─────────────────────────────────────────────────────────────────────


def test_each_record_reads_in_the_offset_of_its_own_instant_across_daylight_saving(
    folder,
):
    # Berlin leaves summer time at 01:00 UTC on 2026-10-25: 02:30 happens twice.
    folder.write(
        record(1, at=datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc))
        + record(2, at=datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc))
        + record(3, at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc))
    )
    seen = run(folder, local=ZoneInfo("Europe/Berlin"))
    assert [line[:19] for line in seen.lines] == [
        "2026-10-25 02:30:00",
        "2026-10-25 02:30:00",
        "2026-07-01 14:00:00",
    ]


def test_a_record_written_with_another_offset_is_converted_to_local_time(folder):
    at = datetime(2026, 10, 18, 7, 4, 5, 123456, tzinfo=timezone(timedelta(hours=-5)))
    folder.write(record(1, at=at))
    assert run(folder, local=ZoneInfo("Europe/Minsk")).lines == ["2026-10-18 15:04:05  info  request  GET /v1/status 1"]


# ── Follow ───────────────────────────────────────────────────────────────────


def test_follow_prints_new_lines_as_they_are_written_and_ctrl_c_exits_0(folder):
    folder.write(record(1))
    seen = run(folder, follow=True, steps=[
        lambda: folder.append(record(2)),
        lambda: None,
        lambda: folder.append(record(3) + record(4)),
    ])
    assert seen.code == EXIT_OK
    assert numbers(seen.lines) == [1, 2, 3, 4]


def test_follow_across_one_rotation_reads_the_old_file_to_its_end_then_the_new(folder):
    folder.write(record(1))

    def rotate() -> None:
        folder.append(record(2))  # written to the old file just before rotating
        folder.rotate()
        folder.write(record(3))

    seen = run(folder, follow=True, steps=[rotate, lambda: folder.append(record(4))])
    assert numbers(seen.lines) == [1, 2, 3, 4]


def test_follow_across_two_rotations_between_checks_catches_up_through_dot_1(folder):
    folder.write(record(1))

    def twice() -> None:
        folder.append(record(2))
        folder.rotate()
        folder.write(record(3) + record(4))
        folder.rotate()
        folder.write(record(5))

    seen = run(folder, follow=True, steps=[twice, lambda: folder.append(record(6))])
    assert numbers(seen.lines) == [1, 2, 3, 4, 5, 6]


def test_follow_waits_out_a_moment_with_no_daemon_log(folder):
    folder.write(record(1))

    def renamed() -> None:
        folder.append(record(2))
        folder.rotate()

    seen = run(folder, follow=True, steps=[
        renamed,
        lambda: None,
        lambda: folder.write(record(3)),
        lambda: folder.append(record(4)),
    ])
    assert numbers(seen.lines) == [1, 2, 3, 4]


def test_follow_with_no_file_yet_says_so_then_prints_the_first_lines(folder):
    seen = run(folder, follow=True, steps=[
        lambda: None,
        lambda: folder.write(record(1) + record(2)),
        lambda: folder.append(record(3)),
    ])
    assert seen.code == EXIT_OK
    err = seen.err.splitlines()
    assert err[0].startswith("pm-ai: there is no daemon log yet")
    assert "~/.pm-ai/logs/daemon.log" in err[0] and "`pm-ai daemon run`" in err[0]
    assert err[1].startswith("pm-ai: reading ~/.pm-ai/logs/daemon.log")
    assert numbers(seen.lines) == [1, 2, 3]


def test_follow_with_no_file_yet_and_a_rotation_soon_after_loses_nothing(folder):
    def started_and_rotated() -> None:
        folder.write(record(1))

    def rotated() -> None:
        folder.append(record(2))
        folder.rotate()
        folder.write(record(3))

    seen = run(folder, follow=True, steps=[started_and_rotated, rotated])
    assert numbers(seen.lines) == [1, 2, 3]


# ── Partial, error, planted, foreign ─────────────────────────────────────────


def test_a_partial_last_line_is_held_not_counted_and_printed_once_complete(folder):
    whole = record(2)
    folder.write(record(1) + whole[:20])
    seen = run(folder)
    assert numbers(seen.lines) == [1]
    assert "skipped" not in seen.err
    followed = run(folder, follow=True, steps=[lambda: folder.append(whole[20:])])
    assert numbers(followed.lines) == [1, 2]
    assert "skipped" not in followed.err


def test_an_error_record_prints_its_frames_indented_and_no_exception_text(folder):
    message = (
        "a claimed step failed\nValueError (its text is not logged)\n"
        "  File /pm_ai/app/prompts.py, line 10, in run\n"
        "  File /pm_ai/storage/service.py, line 20, in save"
    )
    folder.write(record(1, level="error", logger=DAEMON, message=message))
    assert run(folder).lines == [
        "2026-10-18 07:00:01  error  daemon  a claimed step failed",
        "    ValueError (its text is not logged)",
        "      File /pm_ai/app/prompts.py, line 10, in run",
        "      File /pm_ai/storage/service.py, line 20, in save",
    ]


def test_a_planted_escape_is_printed_with_a_question_mark_for_each_control_character(folder):
    planted = "GET /v1/\x1b]0;owned\x07\x1b[2J\x9b31m\x7f\r\x00x 401"
    folder.write(record(1, message=planted, logger="evil\x1b[1m"))
    (line,) = run(folder).lines
    assert line == "2026-10-18 07:00:01  info  evil?[1m  GET /v1/?]0;owned??[2J?31m???x 401"
    assert not any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in line)


def test_a_lone_surrogate_cannot_break_the_output(folder):
    folder.write(b'{"at": "2026-10-18T07:00:01+00:00", "logger": "pm_ai.api.requests", '
                 b'"level": "info", "message": "GET /\\ud800 404"}\n')
    assert run(folder).lines == ["2026-10-18 07:00:01  info  request  GET /? 404"]


def test_foreign_lines_are_never_printed_and_counted_in_one_sentence(folder):
    foreign = [
        b"not json at all\n",
        b"\xff\xfe not utf-8\n",
        b'["a", "list"]\n',
        record(1, at=datetime(2026, 10, 18, 7, 0)),  # naive
        b'{"at": "yesterday", "level": "info", "message": "m"}\n',
        record(2, level="loud"),
        record(3, level="INFO"),
        b'{"at": "2026-10-18T07:00:01+00:00", "level": "info", "message": 7}\n',
        b'{"at": "2026-10-18T07:00:01+00:00", "level": "info"}\n',
    ]
    folder.write(b"".join(foreign) + record(9))
    seen = run(folder)
    assert numbers(seen.lines) == [9]
    assert seen.err.splitlines()[-1] == (
        f"pm-ai: {len(foreign)} lines were not in pm-ai's log format and were skipped."
    )


def test_one_foreign_line_is_one_line(folder):
    folder.write(b"oops\n" + record(1))
    assert run(folder).err.splitlines()[-1] == (
        "pm-ai: 1 line was not in pm-ai's log format and was skipped."
    )


def test_a_secret_in_any_field_but_message_is_never_printed(folder):
    folder.write(record(1, path="/v1/status?token=SECRET", token="SECRET", status=401,
                        exc_text="ValueError: SECRET"))
    seen = run(folder)
    assert "SECRET" not in seen.out and "SECRET" not in seen.err
    assert seen.lines == ["2026-10-18 07:00:01  info  request  GET /v1/status 1"]


def test_a_logger_neither_of_the_two_is_shown_by_its_name(folder):
    folder.write(record(1, logger="pm_ai.other"))
    assert run(folder).lines[0].split("  ")[2] == "pm_ai.other"


# ── No file, unreadable, piped, interrupted ──────────────────────────────────


def test_no_file_is_one_sentence_naming_the_path_and_daemon_run(folder):
    seen = run(folder)
    assert seen.code == EXIT_OK
    assert seen.out == ""
    assert seen.err == (
        "pm-ai: there is no daemon log yet. The daemon writes it at "
        "~/.pm-ai/logs/daemon.log when it runs (`pm-ai daemon run`).\n"
    )


def test_an_unreadable_current_file_is_refused_naming_it_and_permission_denied(folder):
    folder.write(record(1))
    folder.path().chmod(0)
    try:
        with pytest.raises(logs.LogUnreadable) as refused:
            run(folder)
    finally:
        folder.path().chmod(0o600)
    assert str(refused.value) == (
        "the daemon log ~/.pm-ai/logs/daemon.log could not be read (Permission denied)."
    )


def test_an_unreadable_current_file_through_dispatch_is_exit_3(folder, capsys):
    folder.write(record(1))
    folder.path().chmod(0)
    parsed = parse(["logs"])
    try:
        code = dispatch(parsed, daemon=None, diagnose=lambda: Report(()),
                        probe_connectors=None, daemon_log=folder.log)
    finally:
        folder.path().chmod(0o600)
    assert code == EXIT_REFUSAL
    assert "Permission denied" in capsys.readouterr().err


class _ClosesAfter(io.StringIO):
    """A pipe whose reader went away after `n` writes (`| head`)."""

    def __init__(self, n: int) -> None:
        super().__init__()
        self.left = n

    def write(self, text: str) -> int:
        if self.left <= 0:
            raise BrokenPipeError(32, "Broken pipe")
        self.left -= 1
        return super().write(text)


def test_output_closed_early_is_exit_0_and_nothing_more_printed(folder):
    folder.write(b"oops\n" + record(1))
    seen = run(folder, out=_ClosesAfter(0))
    assert seen.code == EXIT_OK
    assert "skipped" not in seen.err, "nothing more is printed once the output closed"


def test_output_closed_while_following_is_exit_0(folder):
    folder.write(record(1))
    seen = run(folder, follow=True, out=_ClosesAfter(1),
               steps=[lambda: folder.append(record(2)), lambda: folder.append(record(3))])
    assert seen.code == EXIT_OK


def test_ctrl_c_without_follow_is_left_to_main(folder, monkeypatch):
    folder.write(record(1))

    def interrupted(*_: object, **__: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(logs, "format_record", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(folder)


def test_the_file_is_never_written(folder):
    folder.write(record(1), ".1")
    folder.write(record(2) + b"partial")
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.root.iterdir()}
    run(folder, follow=True, steps=[lambda: None])
    after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.root.iterdir()}
    assert after == before


# ── The command ──────────────────────────────────────────────────────────────


def test_logs_is_in_the_table_with_its_options_and_follow_flag_and_no_target():
    command = TABLE["logs"]
    assert command.options == ("lines", "level")
    assert command.flags == ("follow",)
    assert command.target is None
    assert command.reads_daemon_log and not command.composes


def test_logs_needs_this_terminal():
    from pm_ai.surfaces.cli.dispatch import TERMINAL_BOUND
    from pm_ai.surfaces.cli.repl import runs_here

    assert ("logs",) in TERMINAL_BOUND
    assert runs_here(["logs", "--follow"])


def test_the_level_completes_the_five_levels_and_lines_offers_nothing():
    from pm_ai.surfaces.cli.completion import Completion, Readers

    completion = Completion(Readers())
    assert [c.text for c in completion.complete("logs --level ")] == list(logs.LEVELS)
    assert [c.text for c in completion.complete("logs --level w")] == ["warning"]
    assert [c.text for c in completion.complete("logs --lines ")] == []
    assert set(c.text for c in completion.complete("logs ")) == {"--lines", "--level", "--follow"}


@pytest.mark.parametrize(
    ("configured", "named"),
    [("Europe/Minsk", "Europe/Minsk"), (":Asia/Tokyo", "Asia/Tokyo"), ("UTC", "UTC"),
      ("EST5EDT", "EST5EDT"), ("", "UTC"), ("/usr/share/zoneinfo/Europe/Berlin", "Europe/Berlin"),
     (":/usr/share/zoneinfo/Asia/Tokyo", "Asia/Tokyo"), ("/etc/somewhere/Berlin", None),
     ("Foo/Bar", None), ("CET-1CEST", None)],
)
def test_the_machine_zone_is_named_from_tz_first(monkeypatch, configured, named):
    from pm_ai.platform.environment import local_zone_name

    monkeypatch.setenv("TZ", configured)
    assert local_zone_name() == named


@pytest.mark.parametrize(
    ("link", "named"),
    [("/var/db/timezone/zoneinfo/Europe/Minsk", "Europe/Minsk"),
     ("/usr/share/zoneinfo/Foo/Bar", None),
     ("/etc/some/localtime", None),
     (OSError(22, "Invalid argument"), None)],
)
def test_without_tz_the_zone_is_where_etc_localtime_links(monkeypatch, link, named):
    from pm_ai.platform import environment

    monkeypatch.delenv("TZ", raising=False)

    def readlink(path):
        assert str(path) == "/etc/localtime"
        if isinstance(link, BaseException):
            raise link
        return link

    monkeypatch.setattr(environment.os, "readlink", readlink)
    assert environment.local_zone_name() == named


# ── Review round: bounds, header, order, races ───────────────────────────────


@pytest.mark.parametrize(
    ("at", "local"),
    [("0001-01-01T00:00:00+05:00", "UTC"), ("9999-12-31T23:59:59+00:00", "Asia/Tokyo"),
     ("0001-01-01T00:30:00+00:00", "America/New_York")],
)
def test_an_instant_the_zone_cannot_reach_is_a_foreign_line(folder, at, local):
    folder.write(
        b'{"at": "' + at.encode() + b'", "logger": "pm_ai.daemon", "level": "info", '
        b'"message": "edge"}\n' + record(1)
    )
    seen = run(folder, local=ZoneInfo(local))
    assert seen.code == EXIT_OK
    assert numbers(seen.lines) == [1]
    assert seen.err.splitlines()[-1] == (
        "pm-ai: 1 line was not in pm-ai's log format and was skipped."
    )


def test_the_first_line_names_only_the_copies_read(folder):
    folder.write(record(1))
    assert run(folder).err.splitlines()[0].startswith(
        "pm-ai: reading ~/.pm-ai/logs/daemon.log, times in"
    )
    folder.write(record(0), ".1")
    assert "(and daemon.log.1)," in run(folder).err.splitlines()[0]
    folder.write(record(0), ".2")
    assert "(and daemon.log.1–.2)," in run(folder).err.splitlines()[0]
    folder.write(record(0), ".3")
    assert "(and daemon.log.1–.3)," in run(folder).err.splitlines()[0]


def test_the_first_line_when_the_file_appears_later_names_no_copies(folder):
    seen = run(folder, follow=True, steps=[lambda: folder.write(record(1))])
    assert seen.err.splitlines()[1].startswith(
        "pm-ai: reading ~/.pm-ai/logs/daemon.log, times in"
    )


def test_unreadable_copy_notices_are_oldest_first_with_or_without_a_current_file(folder):
    for suffix in (".1", ".3"):
        folder.write(record(1), suffix)
        folder.path(suffix).chmod(0)
    try:
        without = run(folder)
        folder.write(record(2))
        with_current = run(folder)
    finally:
        for suffix in (".1", ".3"):
            folder.path(suffix).chmod(0o600)

    def notices(text: str) -> list[str]:
        return [line for line in text.splitlines() if "was skipped" in line]

    expected = [
        "pm-ai: ~/.pm-ai/logs/daemon.log.3 could not be read and was skipped.",
        "pm-ai: ~/.pm-ai/logs/daemon.log.1 could not be read and was skipped.",
    ]
    assert notices(without.err) == expected
    assert notices(with_current.err) == expected


def test_the_skipped_count_is_reported_before_a_refusal_while_following(folder, monkeypatch):
    folder.write(b"oops\n" + record(1))

    def unreadable() -> None:
        folder.append(b"again\n")
        folder.rotate()
        folder.write(record(2))
        folder.path().chmod(0)

    try:
        with pytest.raises(logs.LogUnreadable):
            errors = io.StringIO()
            remaining = [unreadable]
            logs.show(folder.log, follow=True, out=io.StringIO(), err=errors,
                      sleep=lambda _: remaining.pop(0)(), local=UTC)
    finally:
        folder.path().chmod(0o600)
    assert errors.getvalue().splitlines()[-1] == (
        "pm-ai: 2 lines were not in pm-ai's log format and were skipped."
    )


def test_a_rotation_just_after_the_copies_are_read_loses_nothing(folder, monkeypatch):
    """The daemon rotates again between the copy walk and the next look."""
    folder.write(record(1))
    real = logs._newer_copies
    calls: list[int] = []

    def then_rotated(*arguments, **options):
        real(*arguments, **options)
        if not calls:
            calls.append(1)
            folder.append(record(4))  # into the file now current
            folder.rotate()
            folder.write(record(5))

    monkeypatch.setattr(logs, "_newer_copies", then_rotated)

    def rotated() -> None:
        folder.append(record(2))
        folder.rotate()
        folder.write(record(3))

    seen = run(folder, follow=True, steps=[rotated, lambda: folder.append(record(6))])
    assert numbers(seen.lines) == [1, 2, 3, 4, 5, 6]


def test_a_rotation_while_the_copies_are_opened_prints_each_record_once(folder, monkeypatch):
    folder.write(b"".join(record(n) for n in range(0, 10)), ".2")
    folder.write(b"".join(record(n) for n in range(10, 20)), ".1")
    folder.write(b"".join(record(n) for n in range(20, 30)))
    real = logs._open
    calls: list[int] = []

    def opened_then_rotated(path, log):
        handle = real(path, log)
        if not calls:
            calls.append(1)
            folder.rotate()
            folder.write(record(99))
        return handle

    monkeypatch.setattr(logs, "_open", opened_then_rotated)
    assert numbers(run(folder, lines=100).lines) == list(range(30))


def test_follow_filters_followed_lines_by_level_too(folder):
    folder.write(record(1, level="info") + record(2, level="error"))
    seen = run(folder, follow=True, level="warning", steps=[
        lambda: folder.append(record(3, level="debug") + record(4, level="warning")),
        lambda: folder.append(record(5, level="info") + record(6, level="critical")),
    ])
    assert numbers(seen.lines) == [2, 4, 6]
