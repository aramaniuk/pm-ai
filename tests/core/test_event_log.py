"""One vocabulary over a scope's segments (derivation-services.md, rule 3).

Spec: `_bmad-output/specs/spec-pm-ai/stories/2h-event-log-accessor.md`.

Every test drives the accessor through a fake `StoragePort`. That is the point of
the design rather than a convenience: an accessor that needed a filesystem to be
exercised would be holding a path, and rule 3 exists so that four readers of
`event_log/` do not each parse it their own way.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pm_ai.core import ledger
from pm_ai.core.event_log import EventLog
from pm_ai.domain.event_entries import EventEntry, SelfActionType, render_entry
from pm_ai.domain.identity import DataScope, ScopeKind

SCOPE = DataScope(ScopeKind.PERSONAL)
OTHER = DataScope(ScopeKind.APPLICATION)


def _line(entry_id: str, ingested_at: str, marker: str) -> str:
    return render_entry(
        EventEntry(
            entry_id=entry_id,
            category=SelfActionType.SECURITY,
            actor="test",
            fields=(("ingested_at", ingested_at), ("detail", marker)),
        )
    )


class FakeStorage:
    """Only what `EventLog` is allowed to reach: the port, not the service."""

    def __init__(self, segments=None):
        self.segments = segments or {}
        self.appended: list[tuple[EventEntry, DataScope]] = []
        self.opened: list[str] = []

    def append_event_log(self, entry: EventEntry, *, scope: DataScope) -> None:
        self.appended.append((entry, scope))

    def event_log_segments(self, *, scope: DataScope) -> tuple[str, ...]:
        return tuple(sorted(self.segments.get(scope, {})))

    def read_event_log_segment(self, *, scope: DataScope, name: str) -> str:
        self.opened.append(name)
        return self.segments[scope][name]


def _log(segments=None):
    storage = FakeStorage(segments)
    return EventLog(storage), storage


# ── It is a vocabulary, not a second writer ─────────────────────────────────


def test_appending_delegates_to_the_single_writer():
    log, storage = _log()
    entry = EventEntry(category=SelfActionType.SECURITY, actor="pm-ai")

    log.append(entry, scope=SCOPE)

    assert storage.appended == [(entry, SCOPE)]


def test_the_scope_is_an_argument_not_a_construction_time_default():
    """The debug-flag entry goes to the application scope and a skill entry does
    not; a bound scope makes one of those a mistake nobody sees."""
    log, storage = _log()
    entry = EventEntry(category=SelfActionType.SECURITY, actor="pm-ai")

    log.append(entry, scope=SCOPE)
    log.append(entry, scope=OTHER)

    assert [scope for _, scope in storage.appended] == [SCOPE, OTHER]


# ── Reading spans segments, in arrival order ────────────────────────────────


def test_reading_spans_every_segment_in_arrival_order():
    log, _ = _log(
        {
            SCOPE: {
                "2026-07.md": _line("evt_1", "2026-07-01T00:00:00+00:00", "july") + "\n",
                "2026-08.md": (
                    _line("evt_2", "2026-08-01T00:00:00+00:00", "aug-first") + "\n"
                    + _line("evt_3", "2026-08-02T00:00:00+00:00", "aug-second") + "\n"
                ),
            }
        }
    )
    assert [dict(e.fields)["detail"] for e in log.read(scope=SCOPE)] == [
        "july",
        "aug-first",
        "aug-second",
    ]


def test_arrival_order_is_not_fold_order():
    """The break: a default that silently reorders for a caller asking what happened."""
    log, _ = _log(
        {
            SCOPE: {
                "2026-08.md": (
                    _line("evt_z", "2026-08-01T00:00:00+00:00", "written-first") + "\n"
                    + _line("evt_a", "2026-08-02T00:00:00+00:00", "written-second") + "\n"
                )
            }
        }
    )
    read = log.read(scope=SCOPE)
    assert [e.entry_id for e in read] == ["evt_z", "evt_a"], "not arrival order"
    assert [e.entry_id for e in ledger.fold(read)] == ["evt_a", "evt_z"], (
        "fold must reorder these — they carry no occurred_at, so it ties on the "
        "id. That is the whole reason it cannot be the default for a read."
    )


def test_an_empty_log_reads_empty_and_opens_nothing():
    log, storage = _log()
    assert log.read(scope=SCOPE) == ()
    assert storage.opened == []


def test_a_truncated_tail_is_dropped_not_raised():
    log, _ = _log(
        {SCOPE: {"2026-08.md": _line("evt_1", "2026-08-01T00:00:00+00:00", "whole") + "\n"
                 + "- [evt_2] security actor=test ingested"}}
    )
    assert [e.entry_id for e in log.read(scope=SCOPE)] == ["evt_1"]


# ── A range is bounded by the write clock, and says so ──────────────────────


def test_a_range_excludes_entries_outside_it():
    log, _ = _log(
        {
            SCOPE: {
                "2026-07.md": _line("evt_1", "2026-07-15T00:00:00+00:00", "july") + "\n",
                "2026-08.md": _line("evt_2", "2026-08-15T00:00:00+00:00", "august") + "\n",
            }
        }
    )
    read = log.read(
        scope=SCOPE, ingested_since=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    assert [dict(e.fields)["detail"] for e in read] == ["august"]


def test_a_range_does_not_open_segments_outside_it():
    """The promise the parameter's name makes good: filenames derive from the
    same clock the range is on, so skipping them is sound."""
    log, storage = _log(
        {
            SCOPE: {
                "2026-06.md": _line("evt_0", "2026-06-15T00:00:00+00:00", "june") + "\n",
                "2026-07.md": _line("evt_1", "2026-07-15T00:00:00+00:00", "july") + "\n",
                "2026-08.md": _line("evt_2", "2026-08-15T00:00:00+00:00", "august") + "\n",
            }
        }
    )
    log.read(scope=SCOPE, ingested_since=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert storage.opened == ["2026-08.md"]


def test_a_range_is_inclusive_of_its_bounds():
    at = "2026-08-15T00:00:00+00:00"
    log, _ = _log({SCOPE: {"2026-08.md": _line("evt_1", at, "on-the-bound") + "\n"}})
    bound = datetime(2026, 8, 15, tzinfo=timezone.utc)
    assert len(log.read(scope=SCOPE, ingested_since=bound, ingested_until=bound)) == 1


def test_an_entry_without_an_ingestion_time_is_excluded_from_a_range():
    """Not silently included: a range asks a question it cannot answer for that
    entry, and guessing either way is worse than leaving it out."""
    line = render_entry(
        EventEntry(entry_id="evt_1", category=SelfActionType.SECURITY, actor="test")
    )
    log, _ = _log({SCOPE: {"2026-08.md": line + "\n"}})

    assert len(log.read(scope=SCOPE)) == 1
    assert log.read(scope=SCOPE, ingested_since=datetime(2026, 1, 1, tzinfo=timezone.utc)) == ()


# ── Which segment is open ───────────────────────────────────────────────────


def test_the_open_segment_is_the_newest_present():
    log, _ = _log({SCOPE: {"2026-07.md": "", "2026-08.md": ""}})
    assert log.open_segment(scope=SCOPE) == "2026-08.md"


def test_an_empty_log_has_no_open_segment():
    log, _ = _log()
    assert log.open_segment(scope=SCOPE) is None
